# MaiBot 概率性自纠错插件

`nxtsl.typo-correction` 是一个 MaiBot 插件，可通过 Tool 或出站 Hook 实现：先发送轻微错字版本，短暂等待后撤回，再发送正确文本。

## 功能

- 默认启用，默认触发概率为 10%
- 默认错误消息停留 800ms
- 支持 MaiBot Tool `probabilistic_typo_correction`
- 支持普通文本出站消息的概率性自纠错
- 自动跳过命令、链接、图片、文件和超长文本
- 使用 NapCat `send_group_msg` / `send_private_msg` 和 `delete_msg`
- 不执行 CMD、PowerShell、Shell 或其它系统命令

## Tool

MaiBot 可以调用：

```text
probabilistic_typo_correction
```

参数：

```text
text: 最终要发送的正确文本
```

## 配置

```toml
[plugin]
config_version = "0.1.0"
enabled = true
probability = 0.10
recall_delay_ms = 800
max_chars = 120
```

- `probability`：自动 Hook 触发概率，范围 `0.0` 到 `1.0`
- `recall_delay_ms`：错字消息停留时间，范围 200 到 5000 毫秒
- `max_chars`：自动 Hook 处理的最大文本长度

## 安装

将插件目录放入 MaiBot 的插件目录，确保目录包含：

```text
__init__.py
_manifest.json
config.toml
plugin.py
```

然后在 MaiBot WebUI 中加载插件或重启 MaiBot。

## 许可证

MIT
