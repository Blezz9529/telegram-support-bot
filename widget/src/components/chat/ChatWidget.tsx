import ChatButton from './ChatButton';
import ChatPopup from './ChatPopup';
import { useChat } from '@/hooks/useChat';

const ChatWidget = () => {
  const { messages, operator, hasUnread, isOpen, sendMessage, openChat, closeChat, menuButtons, sessionState, uiTexts, closeLabel, newDialogLabel, handleMenuClick, handleFeedbackClick, handleClose, handleNewDialog } = useChat();

  return (
    <>
      <ChatButton onClick={openChat} hasUnread={hasUnread} isOpen={isOpen} />
      <ChatPopup
        isOpen={isOpen}
        onClose={closeChat}
        messages={messages}
        operator={operator}
        onSendMessage={sendMessage}
        menuButtons={menuButtons}
        sessionState={sessionState}
        uiTexts={uiTexts}
        closeLabel={closeLabel}
        newDialogLabel={newDialogLabel}
        onMenuClick={handleMenuClick}
        onFeedbackClick={handleFeedbackClick}
        onCloseSession={handleClose}
        onNewDialog={handleNewDialog}
      />
    </>
  );
};

export default ChatWidget;
