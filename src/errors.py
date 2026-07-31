class AgentInputError(ValueError):
    """User input cannot be safely analysed."""


def validate_request(coin: str, question: str) -> tuple[str, str]:
    normalized_coin = coin.strip().upper()
    normalized_question = question.strip()
    if normalized_coin not in {"BTC", "ETH", "SOL", "BNB", "XRP"}:
        raise AgentInputError("目前僅支援 BTC、ETH、SOL、BNB、XRP")
    if not normalized_question:
        raise AgentInputError("研究問題不可為空白")
    if len(normalized_question) > 500:
        raise AgentInputError("研究問題不可超過 500 字")
    return normalized_coin, normalized_question
