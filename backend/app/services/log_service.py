"""
日志拉取服务 — 占位 stub，接入真实日志 API 时替换此文件中的实现。

接口约定：
  fetch_logs(start_time, end_time, keywords) -> str
  返回格式化的日志文本摘要（Markdown 或纯文本），供 AI 分析。
"""
import logging

logger = logging.getLogger(__name__)


async def fetch_logs(
    start_time: str,
    end_time: str,
    keywords: list[str] | None = None,
) -> str:
    """
    拉取指定时间段的系统运行日志。

    TODO: 接入真实日志 API 后替换此实现。
    参数：
      start_time  ISO8601 字符串，如 "2026-04-10T10:00:00"
      end_time    ISO8601 字符串，如 "2026-04-10T10:30:00"
      keywords    可选关键词过滤，如 ["拍照", "超时", "camera"]
    返回：
      日志摘要文本，若无日志则返回空字符串。
    """
    logger.info("fetch_logs called (stub): %s ~ %s, keywords=%s", start_time, end_time, keywords)
    # 占位返回，不影响 AI 分析流程
    return ""
