from aagman_qa.conversation import Message, classify_unknown_roles, latest_assistant_message


def test_classify_unknown_roles_matches_known_user_prompts():
    msgs = [
        Message(role=None, text="Run a backtest for RELIANCE"),
        Message(role=None, text="What time frame should I use?"),
    ]
    known = ["Run a backtest for RELIANCE"]
    classified = classify_unknown_roles(msgs, known)
    assert classified[0].role == "user"
    assert classified[1].role == "assistant"


def test_latest_assistant_message_returns_last_assistant_text():
    msgs = [
        Message(role="user", text="Run a backtest for RELIANCE"),
        Message(role="assistant", text="What time frame should I use?"),
        Message(role="user", text="1 year"),
        Message(role="assistant", text="Running backtest now..."),
    ]
    assert latest_assistant_message(msgs) == "Running backtest now..."


def test_latest_assistant_message_classifies_unknown_texts():
    msgs = [
        Message(role=None, text="Run a backtest"),
        Message(role=None, text="Sure, which symbol?"),
    ]
    assert latest_assistant_message(msgs, known_user_texts=["Run a backtest"]) == "Sure, which symbol?"
