# -*- coding: utf-8 -*-
"""浏览器可执行文件发现；不访问用户的日常浏览器配置档。"""
import os
import sys


def app_data_dir():
    """返回挂件私有数据目录（调用方负责按需创建）。"""
    if sys.platform == 'darwin':
        base = os.path.expanduser('~/Library/Application Support')
    else:
        base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
    return os.path.join(base, 'WBCreditWidget')


def find_browser():
    """寻找已安装的 Edge 或 Chrome；缺失时给出可直接显示的错误。"""
    candidates = []
    if sys.platform == 'darwin':
        for app, executable in (('Microsoft Edge', 'Microsoft Edge'),
                                ('Google Chrome', 'Google Chrome')):
            for root in ('/Applications', os.path.expanduser('~/Applications')):
                candidates.append(os.path.join(root, app + '.app', 'Contents',
                                               'MacOS', executable))
    elif sys.platform == 'win32':
        roots = [os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)'),
                 os.environ.get('ProgramFiles', r'C:\Program Files'),
                 os.environ.get('LOCALAPPDATA', '')]
        for relative in (('Microsoft', 'Edge', 'Application', 'msedge.exe'),
                         ('Google', 'Chrome', 'Application', 'chrome.exe')):
            candidates.extend(os.path.join(root, *relative) for root in roots if root)
    else:
        raise OSError('登录功能目前支持 macOS 和 Windows。')
    for path in candidates:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    raise FileNotFoundError('未找到 Microsoft Edge 或 Google Chrome。请先安装其中一个浏览器，再点击登录。')
