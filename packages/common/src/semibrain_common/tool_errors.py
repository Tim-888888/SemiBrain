"""Allowlisted tool corrections, never arbitrary upstream error bodies."""

SANDBOX_ARGUMENT_ERRORS = {
    "SANDBOX_PATH_DENIED": (
        "导出文件名不安全或过长。可用中文、英文、数字、空格、下划线、短横线和括号；"
        "只传文件名，不含路径、控制字符、连续点或系统保留名；最多120字符且UTF-8不超过240字节。"
        "同步修改代码中的输出文件名与exports后重试，无需重新读取已取得的原文。"
    ),
    "UNSUPPORTED_ARTIFACT": "导出仅支持png/csv/json/md/txt；不能只改扩展名冒充其他格式。",
    "RESERVED_FILENAME": "semibrain_前缀供运行时使用，请同步修改代码与exports中的输出文件名。",
}
