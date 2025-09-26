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
    type_map: Optional[dict] = field(default_factory=dict)
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
        cfg.generate_config.xml_path = 'mappers'
        cfg.output_mode = OutputMode.package.name
        return cfg

    @staticmethod
    def empty_config():
        cfg = Configuration()
        cfg.name = ""
        cfg.db = DbConfig()
        cfg.generate_config = GenerateConfig()
        cfg.generate_config.type_map = DEFAULT_TYPE_MAP
        cfg.generate_config.xml_path = 'mappers'
        cfg.output_mode = OutputMode.package.name
        return cfg

    @staticmethod
    def load_from_file(file_path) -> []:
        try:
            if os.path.exists(file_path):
                with open(file_path, 'r', encoding='utf-8') as f:
                    js_config = json.load(f)
                    result = []
                    for item in js_config:
                        config_obj = Configuration()
                        config_obj.name = item.get('name')
                        config_obj.output_mode = item.get('output_mode')
                        config_obj.output_path = item.get('output_path')

                        db_js = item.get('db')
                        config_obj.db = DbConfig()
                        db = config_obj.db
                        db.host = db_js.get('host')
                        db.port = int(db_js.get('port') or 3306)
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
        for key in sorted(self.type_map.keys(), key=len, reverse=True):
            if key in mysql_type:
                return self.type_map[key]
        return "Object"

    def generate_code(self, table, columns):
        # 生成实体类
        entity_content = self._render_template(
            "Entity.java.j2",
            table=table, columns=columns,
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

        output_base = Path(self.config.output_path)
        temp_dir = output_base / "temp"

        # 根据输出模式确定最终路径
        if self.config.output_mode == OutputMode.package.name:
            # 压缩包模式，文件先写入临时目录
            base_write_path = temp_dir
            java_root = base_write_path / "java"
            resources_root = base_write_path / "resources"
        else:
            # 直接写入模式
            base_write_path = output_base
            java_root = base_write_path / "java"
            resources_root = base_write_path / "resources"

        # 拼接完整路径
        entity_dir = java_root / entity_path
        dao_dir = java_root / dao_path
        xml_dir = resources_root / xml_path

        # 创建目录
        entity_dir.mkdir(parents=True, exist_ok=True)
        dao_dir.mkdir(parents=True, exist_ok=True)
        xml_dir.mkdir(parents=True, exist_ok=True)

        entity_name = big_camel_case_filter(table)

        # 写入文件
        (entity_dir / f"{entity_name}.java").write_text(entity_content, encoding='utf-8')
        (dao_dir / f"{entity_name}Mapper.java").write_text(dao_content, encoding='utf-8')
        (xml_dir / f"{entity_name}Mapper.xml").write_text(xml_content, encoding='utf-8')

    def _render_template(self, template_name, **context):
        template = self.jinja_env.get_template(template_name)
        return template.render(**context)


class App(tk.Tk):
    def __init__(self, file_path):
        super().__init__()
        self.title("MyBatis代码生成器")

        # 设置窗口的最小尺寸，防止缩得太小导致控件错乱
        self.minsize(450, 600)
        # 配置主窗口的网格权重
        # 第1列（包含大部分输入框）权重设为1，使其可以水平拉伸
        self.grid_columnconfigure(1, weight=1)
        # 第1行（包含表选择区域）权重设为1，使其可以垂直拉伸
        self.grid_rowconfigure(1, weight=1)

        self.file_path = file_path
        self.config_list = Configuration.load_from_file(file_path)
        self.generator = None
        # 当前配置索引
        self.active_config_index = None
        self._setup_ui()
        self._load_last_config()

    def _setup_ui(self):
        # 使用一个局部变量来跟踪行号，比原来的全局 index 更清晰
        row_index = 0

        # --- 数据库配置区域 ---
        db_config_frame = ttk.LabelFrame(self, text="数据库配置")
        # 修改: 使用 sticky='ew' 让控件横向填充，columnspan=3 让其跨越3列
        db_config_frame.grid(row=row_index, column=0, columnspan=3, padx=10, pady=5, sticky="ew")
        row_index += 1

        # 为容器配置列权重，让输入框列可以拉伸
        db_config_frame.grid_columnconfigure(1, weight=1)

        ttk.Label(db_config_frame, text="选择配置:").grid(row=0, column=0, sticky="w", padx=5, pady=2)
        self.datasource_var = tk.StringVar()
        self.datasource_list = list(map(lambda x: x.name, self.config_list))
        self.datasource_var.set(self.datasource_list[0] if self.datasource_list else "")
        self.datasource_combo = ttk.Combobox(db_config_frame,
                                             textvariable=self.datasource_var,
                                             values=self.datasource_list)
        if self.datasource_list:
            self.datasource_combo.current(0)
            self.active_config_index = 0
        self.datasource_combo.bind("<<ComboboxSelected>>", self._on_combobox_select)
        self.datasource_combo.bind("<FocusOut>", self._check_option_and_update_cfg)
        self.datasource_combo.bind("<Return>", self._check_option_and_update_cfg)

        # 修改: 使用 sticky='ew' 让下拉框横向填充
        self.datasource_combo.grid(row=0, column=1, sticky="ew", padx=5, pady=2)

        ttk.Button(db_config_frame, text="+", width=2, command=self._add_new_config).grid(row=0, column=2, padx=5)
        ttk.Button(db_config_frame, text="-", width=2, command=self._delete_config).grid(row=0, column=3, padx=5)

        self.datasource_fields = ["host", "port", "user", "password", "database"]
        self.entries = {}
        for i, field in enumerate(self.datasource_fields, start=1):
            ttk.Label(db_config_frame, text=field.capitalize() + ":").grid(row=i, column=0, sticky="w", padx=5, pady=2)
            if field == 'password':
                self.show_password = tk.BooleanVar()
                entry = ttk.Entry(db_config_frame, show="*")
                (ttk.Checkbutton(db_config_frame, text="", variable=self.show_password, command=self._toggle_password)
                 .grid(row=i, column=3, sticky="w", padx=5, pady=2))
            else:
                entry = ttk.Entry(db_config_frame)
            entry.bind('<FocusOut>', self._refresh_db_obj)
            entry.bind("<Return>", self._refresh_db_obj)
            # 修改: 使用 sticky='ew' 让输入框横向填充
            entry.grid(row=i, column=1, columnspan=2, sticky="ew", padx=5, pady=2)
            self.entries[field] = entry

        ttk.Button(db_config_frame, text="测试连接", command=self.try_connect_db).grid(
            row=len(self.datasource_fields) + 1, column=1, pady=5)

        # --- 表选择区域 ---
        # 这个区域是垂直拉伸的关键
        table_frame = ttk.LabelFrame(self, text="表选择")
        # 修改: columnspan=3 让其跨越3列, sticky='nsew' 让其填充水平和垂直空间
        table_frame.grid(row=row_index, column=0, columnspan=3, padx=10, pady=5, sticky="nsew")
        row_index += 1

        # --- 新增: 配置 table_frame 内部的网格权重 ---
        table_frame.grid_columnconfigure(0, weight=1)
        table_frame.grid_rowconfigure(1, weight=1)

        # 表操作按钮区域 (位于 table_frame 内部的第0行)
        btn_frame = ttk.Frame(table_frame)
        btn_frame.grid(row=0, column=0, sticky="ew", pady=5)
        ttk.Button(btn_frame, text="全部勾选", width=10, command=self.select_all_tables).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="全部取消", width=10, command=self.deselect_all_tables).pack(side=tk.LEFT, padx=5)

        # 表选择列表框 (位于 table_frame 内部的第1行)
        list_frame = ttk.Frame(table_frame)
        list_frame.grid(row=1, column=0, sticky="nsew")

        # 配置 list_frame 内部的网格权重 ---
        list_frame.grid_rowconfigure(0, weight=1)
        list_frame.grid_columnconfigure(0, weight=1)

        self.table_list = tk.Listbox(list_frame, selectmode="extended", height=8)
        self.table_list.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.table_list.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.table_list.config(yscrollcommand=scrollbar.set)

        # --- 生成配置区域 ---
        gen_config_frame = ttk.LabelFrame(self, text="生成配置")
        gen_config_frame.grid(row=row_index, column=0, columnspan=3, padx=10, pady=5, sticky="ew")
        row_index += 1

        gen_config_frame.grid_columnconfigure(1, weight=1)

        ttk.Label(gen_config_frame, text="输出方式:").grid(row=0, column=0, sticky="w", padx=5, pady=2)
        self.output_mode = tk.StringVar(value=OutputMode.package.name)
        form_frame = tk.Frame(gen_config_frame)
        form_frame.grid(row=0, column=1, columnspan=2, sticky="w")
        rb1 = tk.Radiobutton(form_frame, text="压缩包", variable=self.output_mode, value=OutputMode.package.name)
        rb1.pack(side=tk.LEFT)
        rb2 = tk.Radiobutton(form_frame, text="写入目录", variable=self.output_mode,
                             value=OutputMode.write_into_path.name)
        rb2.pack(side=tk.LEFT)

        ttk.Label(gen_config_frame, text="输出路径:").grid(row=1, column=0, sticky="w", padx=5, pady=2)
        self.output_entry = ttk.Entry(gen_config_frame)
        self.output_entry.grid(row=1, column=1, sticky="ew", padx=(5, 0))
        ttk.Button(gen_config_frame, text="浏览", command=self.browse_path).grid(row=1, column=2, padx=(0, 5))

        ttk.Label(gen_config_frame, text="实体包名:").grid(row=2, column=0, sticky="w", padx=5, pady=2)
        self.entity_package_entry = ttk.Entry(gen_config_frame)
        self.entity_package_entry.grid(row=2, column=1, columnspan=2, sticky="ew", padx=5)

        ttk.Label(gen_config_frame, text="接口包名:").grid(row=3, column=0, sticky="w", padx=5, pady=2)
        self.interface_package_entry = ttk.Entry(gen_config_frame)
        self.interface_package_entry.grid(row=3, column=1, columnspan=2, sticky="ew", padx=5)

        ttk.Label(gen_config_frame, text="xml路径:").grid(row=4, column=0, sticky="w", padx=5, pady=2)
        self.xml_path_entry = ttk.Entry(gen_config_frame)
        self.xml_path_entry.grid(row=4, column=1, sticky="ew", padx=(5, 0))
        ttk.Button(gen_config_frame, text="保存配置", command=self.save_file).grid(row=4, column=2, padx=(0, 5))

        # --- 操作按钮 ---
        action_frame = ttk.Frame(self)
        action_frame.grid(row=row_index, column=0, columnspan=3, pady=10)
        row_index += 1

        start_button = ttk.Button(action_frame, text="生成代码", command=self.generate)
        start_button.pack()

    def _toggle_password(self):
        if self.show_password.get():
            self.entries.get('password').config(show='')
        else:
            self.entries.get('password').config(show="*")

    def _on_combobox_select(self, event):
        self.active_config_index = self.datasource_combo.current()
        datasource_config = self.config_list[self.active_config_index]
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
        if self.active_config_index is not None and self.active_config_index < len(self.config_list):
            config = self.config_list[self.active_config_index]
            self._update_db_cfg_from_info(config)

    def _update_all_cfg_from_info(self, config: Configuration):
        self._update_db_cfg_from_info(config)
        config.output_mode = self.output_mode.get()
        config.output_path = self.output_entry.get()
        config.generate_config.entity_package = self.entity_package_entry.get()
        config.generate_config.dao_package = self.interface_package_entry.get()
        config.generate_config.xml_path = self.xml_path_entry.get()

    def _update_db_cfg_from_info(self, config: Configuration):
        for field, entry in self.entries.items():
            if field == 'host':
                config.db.host = entry.get()
            if field == 'port':
                try:
                    config.db.port = int(entry.get())
                except (ValueError, TypeError):
                    config.db.port = 3306  # Default port if entry is invalid
            if field == 'user':
                config.db.user = entry.get()
            if field == 'password':
                config.db.password = entry.get()
            if field == 'database':
                config.db.database = entry.get()

    def _check_option_and_update_cfg(self, event):
        """
        当Combobox失去焦点或按下回车时，更新配置名称。
        优化点：不使用 .current()，而是使用 self.active_config_index。
        """
        if self.active_config_index is None:
            return  # 如果没有活动索引，则不执行任何操作

        new_name = self.datasource_var.get()
        # 检查新名称是否为空
        if not new_name.strip():
            # 如果名称为空，恢复为旧名称并提示用户
            old_name = self.config_list[self.active_config_index].name
            self.datasource_var.set(old_name)
            messagebox.showwarning("提示", "配置名称不能为空！")
            return

        old_name = self.config_list[self.active_config_index].name

        # 仅当名称发生变化时才更新
        if old_name != new_name:
            # 1. 更新数据模型中的名称
            self.config_list[self.active_config_index].name = new_name

            # 2. 更新Combobox显示列表
            values = list(self.datasource_combo['values'])
            values[self.active_config_index] = new_name
            self.datasource_combo['values'] = values
            print(f"配置名称已从 '{old_name}' 更新为 '{new_name}'")

    def _add_new_config(self):
        new_cfg = Configuration.empty_config()
        new_name_base = "新配置"
        new_name_suffix = 1
        existing_names = {cfg.name for cfg in self.config_list}

        # 确保新名称不重复
        new_name = f"{new_name_base}_{new_name_suffix}"
        while new_name in existing_names:
            new_name_suffix += 1
            new_name = f"{new_name_base}_{new_name_suffix}"

        new_cfg.name = new_name
        self.config_list.append(new_cfg)

        new_options = list(self.datasource_combo['values'])
        new_options.append(new_cfg.name)
        self.datasource_combo['values'] = new_options

        new_index = len(self.config_list) - 1
        self.datasource_combo.current(new_index)

        # 更新活动索引
        self.active_config_index = new_index
        self._update_all_info_from_cfg(new_cfg)

    def _delete_config(self):
        if len(self.config_list) == 0:
            return
        current_index = self.active_config_index
        current_config = self.config_list.pop(current_index)
        options = list(self.datasource_combo['values'])
        options.remove(current_config.name)
        self.datasource_combo['values'] = options
        new_index = len(options) - 1
        self.datasource_combo.current(new_index)

        # 更新活动索引
        self.active_config_index = new_index
        self._update_all_info_from_cfg(self.config_list[new_index])

    def _load_last_config(self):
        if not self.config_list:
            self._add_new_config()
        else:
            self.active_config_index = self.datasource_combo.current()
            if self.active_config_index == -1 and self.config_list:
                self.active_config_index = 0  # 默认指向第一个
                self.datasource_combo.current(0)
            datasource_config = self.config_list[self.active_config_index]
            self._update_all_info_from_cfg(datasource_config)

    def _update_db_info_from_cfg(self, datasource_config):
        for field, entry in self.entries.items():
            entry.delete(0, tk.END)
            value = getattr(datasource_config.db, field, '')
            entry.insert(0, str(value) if value is not None else '')

    def try_connect_db(self):
        try:
            config_index = self.active_config_index
            if config_index == -1:
                messagebox.showerror("错误", "请先选择一个配置")
                return
            config = self.config_list[config_index]
            self._update_all_cfg_from_info(config)  # Ensure current entries are used
            self.generator = CodeGenerator(config)
            conn = self.generator.connect_db(
                config.db.host,
                config.db.port,
                config.db.user,
                config.db.password,
                config.db.database
            )
            tables = self.generator.get_tables(conn)
            self.table_list.delete(0, tk.END)
            for table in tables:
                self.table_list.insert(tk.END, table)
            conn.close()
            # messagebox.showinfo("成功", "数据库连接成功，已加载所有表！")
        except Exception as e:
            messagebox.showerror("连接失败", str(e))

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
        try:
            rs = []
            current_config_index = self.active_config_index
            if current_config_index != -1:
                current_config = self.config_list[current_config_index]
                self._update_all_cfg_from_info(current_config)

            for config in self.config_list:
                rs.append(json.loads(config.to_json(ensure_ascii=False)))

            config_path = Path(self.file_path)
            config_path.parent.mkdir(parents=True, exist_ok=True)

            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(rs, f, indent=4, ensure_ascii=False)
            messagebox.showinfo("成功", f"配置已保存到:\n{config_path.resolve()}")
        except Exception as e:
            messagebox.showerror("保存失败", f"保存配置文件时出错: {e}")

    def generate(self):
        try:
            current_config_index = self.active_config_index
            if current_config_index == -1:
                messagebox.showerror("错误", "请先选择一个配置")
                return

            config = self.config_list[current_config_index]
            self._update_all_cfg_from_info(config)
            self.generator = CodeGenerator(config)

            selected_tables = [self.table_list.get(i) for i in self.table_list.curselection()]
            if not selected_tables:
                messagebox.showwarning("警告", "请选择至少一个表")
                return

            if not self.generator.config.output_path:
                messagebox.showwarning("警告", "请指定输出路径")
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
                self.generator.generate_code(table, columns)
            conn.close()

            if self.generator.config.output_mode == OutputMode.package.name:
                output_path = Path(self.generator.config.output_path)
                temp_folder = output_path / "temp"
                zip_file = output_path / "output.zip"
                zip_folder(str(temp_folder), str(zip_file))
                try:
                    shutil.rmtree(temp_folder)
                except Exception as e:
                    print(f"删除临时文件失败：{e}")

            messagebox.showinfo("成功", f"已生成 {len(selected_tables)} 个表的代码！")
        except Exception as e:
            messagebox.showerror("生成失败", str(e))


if __name__ == "__main__":
    app = App(config_cache_path)
    app.mainloop()