import os
import json
import re
import shutil
import sys
from pathlib import Path

import pymysql
from jinja2 import Environment, FileSystemLoader
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import zipfile

# 默认类型映射配置
DEFAULT_TYPE_MAP = {
    "INT": "Integer",
    "BIGINT": "Long",
    "VARCHAR": "String",
    "DATETIME": "Date",
    "TIMESTAMP": "Date",
    "DECIMAL": "BigDecimal",
    "DOUBLE": "Double",
    "TINYINT(1)": "Boolean",
    "TEXT": "String"
}

config_cache_path = "~/simple_mybatis_generator/config.json"


def zip_folder(folder_path, output_zip):
    """
    压缩文件夹为 ZIP 文件
    :param folder_path: 待压缩的文件夹路径
    :param output_zip: 输出的 ZIP 文件路径
    """
    with zipfile.ZipFile(output_zip, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, _, files in os.walk(folder_path):
            for file in files:
                file_path = os.path.join(root, file)
                # 计算压缩包内的相对路径（保留目录结构）
                arcname = os.path.relpath(file_path, folder_path)
                zipf.write(file_path, arcname)
    print(f"已压缩: {folder_path} -> {output_zip}")


def big_camel_case_filter(s):
    if len(s) > 1:
        s = camel_case_filter(s)
        return f"{str(s[0]).upper()}{s[1:]}"
    elif len(s) == 1:
        return str(s).upper()
    else:
        return s


def camel_case_filter(s):
    """安全的下划线转驼峰过滤器"""
    try:
        if not s or not isinstance(s, str):
            return s
        # 分割并过滤空段
        parts = [word for word in re.split(r'_+', s.strip()) if word]
        if not parts:
            return s
        # 首字母小写 + 后续单词首字母大写
        return parts[0].lower() + ''.join(word.capitalize() for word in parts[1:])
    except Exception as e:
        print(f"驼峰转换失败: {e} | 原始值: {s}")
        return s  # 降级处理


# 判断是否为打包环境
if getattr(sys, 'frozen', False):
    base_dir = sys._MEIPASS  # 临时解压目录
else:
    base_dir = os.path.dirname(__file__)

class CodeGenerator:
    def __init__(self):
        self.config_file = config_cache_path
        self.config = self.load_config()
        self.type_map = DEFAULT_TYPE_MAP
        # 初始化模板工具
        self.jinja_env = Environment(loader=FileSystemLoader(os.path.join(base_dir, "templates")))
        # 自定义驼峰工具
        self.jinja_env.filters['camel_case'] = camel_case_filter
        # 自定义大驼峰工具
        self.jinja_env.filters['big_camel_case'] = big_camel_case_filter
        # mysql data_type转javaType工具
        self.jinja_env.filters['map_java_type'] = self.map_java_type

    def load_config(self):
        try:
            with open(self.config_file, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            return {"db_config": {}, "output_path": "", "type_map": DEFAULT_TYPE_MAP}

    def save_config(self):
        config_path = Path(self.config_file).expanduser()
        if not os.path.exists(config_path):
            config_path.parent.mkdir()
            config_path.touch()
        with open(config_path, 'w') as f:
            json.dump(self.config, f, indent=4)

    def connect_db(self, host, port, user, password, database):
        try:
            conn = pymysql.connect(
                host=host, port=int(port), user=user,
                password=password, database=database, charset='utf8mb4'
            )
            self.config["db_config"] = {"host": host, "port": port, "user": user, "password": password,
                                        "database": database}
            return conn
        except Exception as e:
            raise Exception(f"数据库连接失败: {e}")

    def get_tables(self, conn):
        with conn.cursor() as cursor:
            cursor.execute("SHOW TABLES")
            return [table[0] for table in cursor.fetchall()]

    def get_table_columns(self, conn, table):
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(f"SHOW FULL COLUMNS FROM {table}")
            return [
                {"name": col["Field"], "type": col["Type"], "comment": col["Comment"]}
                for col in cursor.fetchall()
            ]

    def map_java_type(self, mysql_type):
        mysql_type = mysql_type.upper()
        for key, java_type in self.type_map.items():
            if key in mysql_type:
                return java_type
        return "Object"

    def generate_code(self, table, columns, config: dict):
        # 生成实体类
        entity_content = self._render_template(
            "Entity.java.j2",
            table=table, columns=columns,
            # generator=config.get('type_map'),
            daoPackage=config.get('daoPackage'), entityPackage=config.get('entityPackage')
        )
        # 生成Mapper接口
        dao_content = self._render_template(
            "Dao.java.j2",
            table=table,
            daoPackage=config.get('daoPackage'), entityPackage=config.get('entityPackage')
        )
        # 生成XML文件
        xml_content = self._render_template(
            "Mapper.xml.j2",
            table=table, columns=columns, entityPackage=config.get('entityPackage'),
            daoPackage=config.get('daoPackage')
        )
        entity_path = str(config.get('entityPackage')).replace(".", "/")
        dao_path = str(config.get('daoPackage')).replace(".", "/")
        # 保存文件
        os.makedirs(f"{config.get('output_path')}/temp/{entity_path}", exist_ok=True)
        os.makedirs(f"{config.get('output_path')}/temp/{dao_path}", exist_ok=True)
        os.makedirs(f"{config.get('output_path')}/temp/resource/mappers", exist_ok=True)

        with open(f"{config.get('output_path')}/temp/{entity_path}/{big_camel_case_filter(table)}.java", "w") as f:
            f.write(entity_content)
        with open(f"{config.get('output_path')}/temp/{dao_path}/{big_camel_case_filter(table)}Mapper.java", "w") as f:
            f.write(dao_content)
        with open(f"{config.get('output_path')}/temp/resource/mappers/{big_camel_case_filter(table)}Mapper.xml",
                  "w") as f:
            f.write(xml_content)

    def _render_template(self, template_name, **context):
        template = self.jinja_env.get_template(template_name)
        return template.render(**context)


class App(tk.Tk):
    def __init__(self, generator):
        super().__init__()
        self.title("MyBatis代码生成器")
        self.generator = generator
        self._setup_ui()
        self._load_last_config()

    def _setup_ui(self):
        index = 0
        # 数据库配置区域
        ttk.Label(self, text="数据库配置").grid(row=index, column=0, sticky="w")
        fields = ["host", "port", "user", "password", "database"]
        default_values = ["localhost", "3306", "root", "123456", "test"]
        self.entries = {}
        for i, field in enumerate(fields):
            ttk.Label(self, text=field.capitalize() + ":").grid(row=i + 1, column=0)
            entry = ttk.Entry(self)
            entry.grid(row=i + 1, column=1)
            entry.insert(0, default_values[i])
            self.entries[field] = entry
        index += len(fields)

        # 表选择与生成配置
        ttk.Button(self, text="测试连接", command=self.connect_db).grid(row=index + 1, column=1)
        index += 1

        # ========== 表选择区域 ==========
        table_frame = ttk.LabelFrame(self, text="表选择")
        table_frame.grid(row=index + 1, column=0, padx=10, pady=5, sticky="nsew", columnspan=3)
        index += 1
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)

        # 表操作按钮区域
        btn_frame = ttk.Frame(table_frame)
        btn_frame.grid(row=index + 1, column=0, sticky="ew", pady=5)
        index += 1

        ttk.Button(btn_frame, text="全部勾选", width=10,
                   command=self.select_all_tables).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="全部取消", width=10,
                   command=self.deselect_all_tables).pack(side=tk.LEFT, padx=5)

        # 表选择列表框
        list_frame = ttk.Frame(table_frame)
        list_frame.grid(row=index + 1, column=0, sticky="nsew")
        index += 1
        list_frame.grid_rowconfigure(0, weight=1)
        list_frame.grid_columnconfigure(0, weight=1)

        self.table_list = tk.Listbox(list_frame, selectmode="extended", height=8)
        self.table_list.grid(row=index + 1, column=0, sticky="nsew")
        index += 1

        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.table_list.yview)
        scrollbar.grid(row=index + 1, column=1, sticky="ns")
        index += 1
        self.table_list.config(yscrollcommand=scrollbar.set)

        ttk.Label(self, text="输出路径:").grid(row=index + 1, column=0)
        self.output_entry = ttk.Entry(self)
        self.output_entry.grid(row=index + 1, column=1, sticky="ew")
        ttk.Button(self, text="浏览", command=self.browse_path).grid(row=index + 1, column=2)
        index += 1

        ttk.Label(self, text="实体包名:").grid(row=index + 1, column=0)
        self.entity_package_entry = ttk.Entry(self)
        self.entity_package_entry.grid(row=index + 1, column=1, sticky="ew")
        index += 1
        ttk.Label(self, text="接口包名:").grid(row=index + 1, column=0)
        self.interface_package_entry = ttk.Entry(self)
        self.interface_package_entry.grid(row=index + 1, column=1, sticky="ew")
        index += 1

        start_button = ttk.Button(self, text="生成代码", command=self.generate)
        start_button.grid(row=index + 1, column=1)
        index += 1

    def _load_last_config(self):
        config = self.generator.config
        for field, entry in self.entries.items():
            if field in config.get("db_config", {}):
                entry.delete(0, tk.END)
                entry.insert(0, config["db_config"][field])
        self.output_entry.insert(0, config.get("output_path", "./"))
        self.entity_package_entry.insert(0, config.get("entityPackage", "com.example.dao.pojo"))
        self.interface_package_entry.insert(0, config.get('daoPackage', "com.example.dao"))

    def connect_db(self):
        try:
            conn = self.generator.connect_db(
                self.entries["host"].get(),
                self.entries["port"].get(),
                self.entries["user"].get(),
                self.entries["password"].get(),
                self.entries["database"].get()
            )
            tables = self.generator.get_tables(conn)
            self.table_list.delete(0, tk.END)
            for table in tables:
                self.table_list.insert(tk.END, table)
            conn.close()
        except Exception as e:
            messagebox.showerror("错误", str(e))

    def browse_path(self):
        path = filedialog.askdirectory()
        if path:
            self.output_entry.delete(0, tk.END)
            self.output_entry.insert(0, path)

    def select_all_tables(self):
        """全选所有表"""
        self.table_list.selection_set(0, tk.END)

    def deselect_all_tables(self):
        """取消全选"""
        self.table_list.selection_clear(0, tk.END)

    def generate(self):

        try:
            # 保存配置
            self.generator.config.update({
                "output_path": self.output_entry.get(),
                "entityPackage": self.entity_package_entry.get(),
                "daoPackage": self.interface_package_entry.get(),
                # "type_map": self.generator.type_map
            })
            self.generator.save_config()

            # 生成代码
            selected_tables = [self.table_list.get(i) for i in self.table_list.curselection()]
            if not selected_tables:
                messagebox.showwarning("警告", "请选择至少一个表")
                return

            conn = self.generator.connect_db(**self.generator.config["db_config"])
            for table in selected_tables:
                columns = self.generator.get_table_columns(conn, table)
                self.generator.generate_code(
                    table, columns,
                    self.generator.config
                )
            conn.close()
            zip_folder(f"{self.generator.config.get('output_path')}/temp",
                       f"{self.generator.config.get('output_path')}/output.zip")
            try:
                # os.remove(f"{self.generator.config.get('output_path')}/temp")
                shutil.rmtree(f"{self.generator.config.get('output_path')}/temp")
            except Exception as e:
                print(f"删除文件失败：{e.with_traceback()}")
            messagebox.showinfo("成功", f"已生成{len(selected_tables)}个表的代码")
        except Exception as e:
            print(e.with_traceback())
            messagebox.showerror("生成失败", str(e))


if __name__ == "__main__":
    generator = CodeGenerator()
    app = App(generator)
    app.mainloop()
