import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from telegram import InlineKeyboardMarkup
from lighter_bot import (
    cmd_dca,
    cb_dca_confirm,
    cb_dca_cancel,
    _execute_manual_dca,
)


@pytest.fixture
def mock_update():
    update = MagicMock()
    update.effective_chat.id = 123456789
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    return update


@pytest.fixture
def mock_context():
    context = MagicMock()
    context.args = []
    context.bot = MagicMock()
    context.bot.send_message = AsyncMock()
    return context


@pytest.fixture
def mock_callback_update():
    update = MagicMock()
    update.effective_chat.id = 123456789
    query = MagicMock()
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.data = "dca_confirm"
    query.message.message_id = 999
    update.callback_query = query
    return update


@pytest.mark.asyncio
async def test_cmd_dca_no_markets(mock_update, mock_context):
    with patch("lighter_bot.DCA_MARKETS", {}):
        await cmd_dca(mock_update, mock_context)
        mock_update.message.reply_text.assert_called_once()
        assert "⚠️ DCA 종목이 설정되지 않았어요" in mock_update.message.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_cmd_dca_shows_confirmation_keyboard(mock_update, mock_context):
    with patch("lighter_bot.DCA_MARKETS", {"NVDAUSD": 20.0, "TSLAUSD": 30.0}):
        await cmd_dca(mock_update, mock_context)
        mock_update.message.reply_text.assert_called_once()
        args, kwargs = mock_update.message.reply_text.call_args
        msg = args[0]
        assert "DCA 수동 실행 확인" in msg
        assert "NVIDIA" in msg
        assert "Tesla" in msg
        assert "$50" in msg
        
        reply_markup = kwargs.get("reply_markup")
        assert isinstance(reply_markup, InlineKeyboardMarkup)
        buttons = [btn.callback_data for row in reply_markup.inline_keyboard for btn in row]
        assert "dca_confirm" in buttons
        assert "dca_cancel" in buttons


@pytest.mark.asyncio
async def test_cmd_dca_direct_confirm_arg(mock_update, mock_context):
    mock_context.args = ["confirm"]
    with patch("lighter_bot.DCA_MARKETS", {"NVDAUSD": 20.0}), \
         patch("lighter_bot._execute_manual_dca", new_callable=AsyncMock) as mock_exec:
        await cmd_dca(mock_update, mock_context)
        mock_exec.assert_called_once_with(
            123456789,
            mock_context.bot,
            start_msg="🚀 DCA 수동 즉시 실행 시작 (1종목)...",
        )


@pytest.mark.asyncio
async def test_cb_dca_confirm_owner(mock_callback_update, mock_context):
    with patch("lighter_bot._is_owner", return_value=True), \
         patch("lighter_bot.DCA_MARKETS", {"NVDAUSD": 20.0}), \
         patch("lighter_bot._execute_manual_dca", new_callable=AsyncMock) as mock_exec:
        await cb_dca_confirm(mock_callback_update, mock_context)
        mock_callback_update.callback_query.answer.assert_called_once()
        mock_callback_update.callback_query.edit_message_text.assert_called_once_with(
            "⏳ DCA 수동 실행 중 (1종목)..."
        )
        mock_exec.assert_called_once_with(123456789, mock_context.bot)


@pytest.mark.asyncio
async def test_cb_dca_confirm_non_owner(mock_callback_update, mock_context):
    with patch("lighter_bot._is_owner", return_value=False), \
         patch("lighter_bot._execute_manual_dca", new_callable=AsyncMock) as mock_exec:
        await cb_dca_confirm(mock_callback_update, mock_context)
        mock_callback_update.callback_query.answer.assert_called_once_with("권한 없음", show_alert=True)
        mock_exec.assert_not_called()


@pytest.mark.asyncio
async def test_cb_dca_cancel_owner(mock_callback_update, mock_context):
    with patch("lighter_bot._is_owner", return_value=True):
        await cb_dca_cancel(mock_callback_update, mock_context)
        mock_callback_update.callback_query.answer.assert_called_once()
        mock_callback_update.callback_query.edit_message_text.assert_called_once_with(
            "❌ <b>DCA 실행이 취소되었어요.</b>", parse_mode="HTML"
        )


@pytest.mark.asyncio
async def test_execute_manual_dca():
    bot = MagicMock()
    bot.send_message = AsyncMock()
    with patch("lighter_bot.DCA_MARKETS", {"NVDAUSD": 20.0}), \
         patch("lighter_bot.execute_dca", new_callable=AsyncMock, return_value={"mock": True}) as mock_dca, \
         patch("lighter_bot.format_dca_notification", return_value="✅ DCA 완료: NVDA"):
        await _execute_manual_dca(12345, bot, start_msg="시작")
        assert bot.send_message.call_count == 2
        mock_dca.assert_called_once_with("NVDAUSD", 20.0)
