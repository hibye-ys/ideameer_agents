import logging
import sys

DEFAULT_LOG_FORMAT = """%(asctime)s - %(name)s - %(levelname)s - %(message)s"""


def get_logger(
    name: str,
    level: int = logging.INFO,
    log_format: str = DEFAULT_LOG_FORMAT,
    stream=sys.stdout,
) -> logging.Logger:
    """
    지정된 이름과 설정으로 로거를 가져옵니다.

    Args:
        name: 로거의 이름입니다. 일반적으로 __name__을 사용합니다.
        level: 로깅 레벨 (예: logging.INFO, logging.DEBUG).
        log_format: 로그 메시지 형식 문자열.
        stream: 로그를 출력할 스트림 (기본값: sys.stdout).

    Returns:
        설정된 logging.Logger 객체.
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # 핸들러가 이미 추가되었는지 확인하여 중복 로깅 방지
    if not logger.handlers:
        handler = logging.StreamHandler(stream)
        formatter = logging.Formatter(log_format)
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger


if __name__ == """__main__""":
    # 예제 사용법
    logger_main = get_logger(__name__, level=logging.DEBUG)
    logger_main.debug("이것은 디버그 메시지입니다.")
    logger_main.info("이것은 정보 메시지입니다.")
    logger_main.warning("이것은 경고 메시지입니다.")
    logger_main.error("이것은 오류 메시지입니다.")
    logger_main.critical("이것은 심각한 오류 메시지입니다.")

    another_logger = get_logger("another_module", level=logging.INFO)
    another_logger.info("다른 로거에서 보낸 정보 메시지.")
