# -*- coding: utf-8 -*-
"""UI 静态资源包（P1a UI 侧栏化）。

把控制台壳层（tokens.css / icons.js / app.js）外置为 /static 服务，
避免把整页 HTML 塞进 server.py 的 Python 字符串（设计文档 P1a Step1）。
本包只放静态资源；动态拼装发生在 app.js + server.py 的 /static 路由。
"""
