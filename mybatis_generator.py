from enum import Enum
import os
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from dataclasses_json import dataclass_json
from pathlib import Path
from typing import Optional

import pymysql
from jinja2 import Environment, FileSystemLoader
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import zipfile

# 默认类型映射配置
DEFAULT_TYPE_MAP = {
    "INT": "Integer",
    "BIGINT": "Long",
    "CHAR": "String",
    "VARCHAR": "String",
    "DATE": "Date",
    "TIME": "Date",
    "DATETIME": "Date",
    "TIMESTAMP": "Date",
    "DECIMAL": "BigDecimal",
    "FLOAT": "Float",
    "DOUBLE": "Double",
    "TINYINT(1)": "Boolean",
    "TEXT": "String"
}

config_cache_path = "./simple_mybatis_generator/config.json"


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


class OutputMode(Enum):
    package = 1
    write_into_path = 2


@dataclass
@dataclass_json
class DbConfig:
    host: Optional[str] = None
    port: Optional[int] = None
    user: Optional[str] = None
    password: Optional[str] = None
    database: Optional[str] = None

    @staticmethod
    def default_db():
        res = DbConfig()
        res.host = 'localhost'
        res.port = 3306
        res.user = 'root'
        res.password = '123456'
        res.database = 'test'
        return res


@dataclass
@dataclass_json
class GenerateConfig:
    type_map: Optional[dict] = field(default=dict)
    entity_package: Optional[str] = None
    dao_package: Optional[str] = None
    xml_path: Optional[str] = None


@dataclass
@dataclass_json
class Configuration:
    name: Optional[str] = None
    db: Optional[DbConfig] = None
    # 输出模式；1：压缩包，2：直接写入指定目录
    output_mode: Optional[int] = None
    output_path: Optional[str] = None
    generate_config: Optional[GenerateConfig] = None

    @staticmethod
    def default_config():
        cfg = Configuration()
        cfg.name = "默认"
        cfg.db = DbConfig.default_db()
        cfg.generate_config = GenerateConfig()
        cfg.generate_config.type_map = DEFAULT_TYPE_MAP
        cfg.generate_config.xml_path = 'resource/mappers'
        cfg.output_mode = OutputMode.package.name
        return cfg

    @staticmethod
    def empty_config():
        cfg = Configuration()
        cfg.name = ""
        cfg.db = DbConfig()
        cfg.generate_config = GenerateConfig()
        cfg.generate_config.type_map = DEFAULT_TYPE_MAP
        cfg.generate_config.xml_path = 'resource/mappers'
        cfg.output_mode = OutputMode.package.name
        return cfg

    @staticmethod
    def load_from_file(file_path) -> []:
        try:
            if os.path.exists(file_path):
                with open(file_path, 'r') as f:
                    js_config = json.load(f)
                    result = []
                    for item in js_config:
                        config_obj = Configuration()
                        config_obj.name = item.get('name')
                        config_obj.output_mode = item.get('output_mode')

                        db_js = item.get('db')
                        config_obj.db = DbConfig()
                        db = config_obj.db
                        db.host = db_js.get('host')
                        db.port = int(db_js.get('port')) or 3306
                        db.user = db_js.get('user')
                        db.password = db_js.get('password')
                        db.database = db_js.get('database')

                        generate_config_js = item.get('generate_config')
                        config_obj.generate_config = GenerateConfig()
                        generate_config = config_obj.generate_config
                        generate_config.type_map = generate_config_js.get('type_map')
                        generate_config.entity_package = generate_config_js.get('entity_package')
                        generate_config.dao_package = generate_config_js.get('dao_package')
                        generate_config.xml_path = generate_config_js.get('xml_path')
                        result.append(config_obj)
                return result
        except Exception as e:
            print(f"file_path load failed {e}")
        return [Configuration.default_config()]


class CodeGenerator:
    def __init__(self, config: Configuration):
        self.config = config
        self.type_map = self.config.generate_config.type_map
        # 初始化模板工具
        self.jinja_env = Environment(loader=FileSystemLoader(os.path.join(base_dir, "templates")))
        # 自定义驼峰工具
        self.jinja_env.filters['camel_case'] = camel_case_filter
        # 自定义大驼峰工具
        self.jinja_env.filters['big_camel_case'] = big_camel_case_filter
        # mysql data_type转javaType工具
        self.jinja_env.filters['map_java_type'] = self.map_java_type

    def connect_db(self, host, port, user, password, database):
        try:
            conn = pymysql.connect(
                host=host, port=int(port), user=user,
                password=password, database=database, charset='utf8mb4'
            )
            self._refresh_db_config(host, port, user, password, database)
            return conn
        except Exception as e:
            raise Exception(f"数据库连接失败: {e}")

    def _refresh_db_config(self, host, port, user, password, database):
        self.config.db.host = host
        self.config.db.port = int(port)
        self.config.db.user = user
        self.config.db.password = password
        self.config.db.database = database

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

    def generate_code(self, table, columns):
        # 生成实体类
        entity_content = self._render_template(
            "Entity.java.j2",
            table=table, columns=columns,
            # generator=config.get('type_map'),
            daoPackage=self.config.generate_config.dao_package,
            entityPackage=self.config.generate_config.entity_package
        )
        # 生成Mapper接口
        dao_content = self._render_template(
            "Dao.java.j2",
            table=table,
            daoPackage=self.config.generate_config.dao_package,
            entityPackage=self.config.generate_config.entity_package
        )
        # 生成XML文件
        xml_content = self._render_template(
            "Mapper.xml.j2",
            table=table, columns=columns, daoPackage=self.config.generate_config.dao_package,
            entityPackage=self.config.generate_config.entity_package
        )
        entity_path = str(self.config.generate_config.entity_package).replace(".", "/")
        dao_path = str(self.config.generate_config.dao_package).replace(".", "/")
        xml_path = self.config.generate_config.xml_path
        extra_path = "/temp" if self.config.output_mode == OutputMode.package.name else ""
        java_path = "" if self.config.output_mode == OutputMode.package.name else "/java"
        resources_path = "" if self.config.output_mode == OutputMode.package.name else "/resources"
        # 保存文件
        os.makedirs(f"{self.config.output_path}{extra_path}{java_path}/{entity_path}", exist_ok=True)
        os.makedirs(f"{self.config.output_path}{extra_path}{java_path}/{dao_path}", exist_ok=True)
        os.makedirs(f"{self.config.output_path}{extra_path}{resources_path}/{xml_path}", exist_ok=True)

        with open(f"{self.config.output_path}{extra_path}/{entity_path}/{big_camel_case_filter(table)}.java", "w") as f:
            f.write(entity_content)
        with open(f"{self.config.output_path}{extra_path}/{dao_path}/{big_camel_case_filter(table)}Mapper.java",
                  "w") as f:
            f.write(dao_content)
        with open(f"{self.config.output_path}{extra_path}/resource/mappers/{big_camel_case_filter(table)}Mapper.xml",
                  "w") as f:
            f.write(xml_content)

    def _render_template(self, template_name, **context):
        template = self.jinja_env.get_template(template_name)
        return template.render(**context)


class App(tk.Tk):
    def __init__(self, file_path):
        super().__init__()
        self.title("MyBatis代码生成器")
        self.file_path = file_path
        self.config_list = Configuration.load_from_file(file_path)
        self.generator = None
        self._setup_ui()
        self._load_last_config()

    def _setup_ui(self):
        index = 0
        # 数据库配置区域
        ttk.Label(self, text="数据库配置").grid(row=index, column=0, sticky="w")
        self.datasource_var = tk.StringVar()
        self.datasource_list = list(map(lambda x: x.name, self.config_list))
        self.datasource_var.set(self.datasource_list[0])
        self.datasource_combo = ttk.Combobox(self,
                                             textvariable=self.datasource_var,
                                             values=self.datasource_list,
                                             width=18)
        self.datasource_combo.current(0)
        # 绑定事件：当下拉框选项被选中时，调用 _on_combobox_select 方法
        self.datasource_combo.bind("<<ComboboxSelected>>", self._on_combobox_select)
        self.datasource_combo.grid(row=index, column=1)
        self.datasource_combo.bind("<FocusOut>", self._check_option_and_update_cfg)
        self.datasource_combo.bind("<Return>", self._check_option_and_update_cfg)
        ttk.Button(self, text="+", width=1, command=self._add_new_config).grid(row=index, column=2)
        self.datasource_fields = ["host", "port", "user", "password", "database"]

        self.entries = {}
        for i, field in enumerate(self.datasource_fields):
            ttk.Label(self, text=field.capitalize() + ":", ).grid(row=i + 1, column=0)
            entry = ttk.Entry(self)
            entry.bind('<FocusOut>', self._refresh_db_obj)
            entry.bind("<Return>", self._refresh_db_obj)
            entry.grid(row=i + 1, column=1)
            # entry.insert(0, default_values[i])
            self.entries[field] = entry
        index += len(self.datasource_fields)

        # 表选择与生成配置
        ttk.Button(self, text="测试连接", command=self.try_connect_db).grid(row=index + 1, column=1)
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

        ttk.Label(self, text="输出方式:").grid(row=index + 1, column=0)
        self.output_mode = tk.StringVar(value=OutputMode.package.name)
        form_frame = tk.Frame(self)
        form_frame.grid(row=index + 1, column=1, sticky="ew")
        rb1 = tk.Radiobutton(form_frame, text="压缩包", variable=self.output_mode, value=OutputMode.package.name)
        rb1.grid(row=0, column=0, sticky="w", padx=(0, 15))
        rb2 = tk.Radiobutton(form_frame, text="写入目录", variable=self.output_mode,
                             value=OutputMode.write_into_path.name)
        rb2.grid(row=0, column=1, sticky="w", padx=(0, 15))
        index += 1

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

        ttk.Label(self, text="xml路径:").grid(row=index + 1, column=0)
        self.xml_path_entry = ttk.Entry(self)
        self.xml_path_entry.grid(row=index + 1, column=1, sticky="ew")
        ttk.Button(self, text="保存配置", command=self.save_file).grid(row=index + 1, column=2)
        index += 1

        start_button = ttk.Button(self, text="生成代码", command=self.generate)
        start_button.grid(row=index + 1, column=1)
        index += 1

    def _on_combobox_select(self, event):
        datasource_config = self.config_list[self.datasource_combo.current()]
        self._update_all_info_from_cfg(datasource_config)

    def _update_all_info_from_cfg(self, datasource_config):
        self._update_db_info_from_cfg(datasource_config)
        self.output_mode.set(datasource_config.output_mode)
        self.output_entry.delete(0, tk.END)
        self.output_entry.insert(0, datasource_config.output_path if datasource_config.output_path else './')
        entity_package = datasource_config.generate_config.entity_package
        self.entity_package_entry.delete(0, tk.END)
        self.entity_package_entry.insert(0, entity_package if entity_package else "com.example.dao.pojo")
        dao_package = datasource_config.generate_config.dao_package
        self.interface_package_entry.delete(0, tk.END)
        self.interface_package_entry.insert(0, dao_package if dao_package else "com.example.dao")
        self.table_list.delete(0, tk.END)
        xml_path = datasource_config.generate_config.xml_path
        self.xml_path_entry.delete(0, tk.END)
        self.xml_path_entry.insert(0, xml_path if xml_path else 'resource/mappers')

    def _refresh_db_obj(self, event):
        config = self.config_list[self.datasource_combo.current()]
        self._update_db_cfg_from_info(config)

    def _update_all_cfg_from_info(self, config: Configuration):
        self._update_db_cfg_from_info(config)
        config.output_mode = self.output_mode.get()
        config.output_path = self.output_entry.get()
        config.generate_config.entity_package = self.entity_package_entry.get()
        config.generate_config.dao_package = self.interface_package_entry.get()
        config.generate_config.xml_path = self.xml_path_entry.get()

    # def
    def _update_db_cfg_from_info(self, config: Configuration):
        for field, entry in self.entries.items():
            if field == 'host':
                config.db.host = entry.get()
            if field == 'port':
                config.db.port = int(entry.get())
            if field == 'user':
                config.db.user = entry.get()
            if field == 'password':
                config.db.password = entry.get()
            if field == 'database':
                config.db.database = entry.get()

    def _check_option_and_update_cfg(self, event):
        new_name = self.datasource_var.get()
        val_list = list(self.datasource_combo['values'])
        if val_list[self.datasource_combo.current()] != new_name:
            val_list[self.datasource_combo.current()] = new_name
            self.datasource_combo['values'] = val_list
            self.config_list[self.datasource_combo.current()].name = new_name

    def _add_new_config(self):
        new_cfg = Configuration.empty_config()
        new_cfg.name = '新配置'
        new_options = list(self.datasource_combo['values'])
        new_options.append(new_cfg.name)
        self.datasource_combo['values'] = new_options
        self.datasource_combo.current(len(self.config_list))
        self.config_list.append(new_cfg)
        self._update_all_info_from_cfg(new_cfg)

    def _load_last_config(self):
        index = self.datasource_combo.current()
        datasource_config = self.config_list[index]
        self._update_all_info_from_cfg(datasource_config)

    def _update_db_info_from_cfg(self, datasource_config):
        for field, entry in self.entries.items():
            entry.delete(0, tk.END)
            entry.insert(0, getattr(datasource_config.db, field, '') or '')

    def try_connect_db(self):
        try:
            config = self.config_list[self.datasource_combo.current()]
            self.generator = CodeGenerator(config)
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
            print(e.with_traceback())
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

    def save_file(self):
        rs = []
        current_config = self.config_list[self.datasource_combo.current()]
        self._update_all_cfg_from_info(current_config)
        for config in self.config_list:
            rs.append(json.loads(config.to_json(ensure_ascii=False)))
        config_path = Path(self.file_path).expanduser()
        if not os.path.exists(config_path):
            if not os.path.exists(config_path.parent):
                config_path.parent.mkdir()
            config_path.touch()
        with open(config_path, 'w') as f:
            json.dump(rs, f, indent=4, ensure_ascii=False)

    def generate(self):

        try:
            # 保存配置
            self._update_all_cfg_from_info(self.generator.config)

            # 生成代码
            selected_tables = [self.table_list.get(i) for i in self.table_list.curselection()]
            if not selected_tables:
                messagebox.showwarning("警告", "请选择至少一个表")
                return

            conn = self.generator.connect_db(
                host=self.generator.config.db.host,
                port=self.generator.config.db.port,
                user=self.generator.config.db.user,
                password=self.generator.config.db.password,
                database=self.generator.config.db.database
            )
            for table in selected_tables:
                columns = self.generator.get_table_columns(conn, table)
                self.generator.generate_code(
                    table, columns)
            conn.close()
            if self.generator.config.output_mode == OutputMode.package.name:
                zip_folder(f"{self.generator.config.output_path}/temp",
                           f"{self.generator.config.output_path}/output.zip")
                try:
                    # os.remove(f"{self.generator.config.get('output_path')}/temp")
                    shutil.rmtree(f"{self.generator.config.output_path}/temp")
                except Exception as e:
                    print(f"删除文件失败：{e.with_traceback()}")
            messagebox.showinfo("成功", f"已生成{len(selected_tables)}个表的代码")
        except Exception as e:
            print(e.with_traceback())
            messagebox.showerror("生成失败", str(e))


if __name__ == "__main__":
    app = App(config_cache_path)
    app.mainloop()