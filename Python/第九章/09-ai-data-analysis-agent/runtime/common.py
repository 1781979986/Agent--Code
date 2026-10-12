"""容器仅需要 SQL 长度助手，不复制模型客户端或服务配置。"""


def utf16_length(text):
    return len(text.encode("utf-16-le", errors="surrogatepass")) // 2
