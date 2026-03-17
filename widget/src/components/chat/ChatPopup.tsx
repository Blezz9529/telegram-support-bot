import { useState, useRef, useEffect, useLayoutEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { X, Send, Paperclip } from 'lucide-react';
import ChatBubble from './ChatBubble';
import TypingIndicator from './TypingIndicator';
import { ChatMessage, Operator } from '@/lib/chatService';

interface ChatPopupProps {
  isOpen: boolean;
  onClose: () => void;
  messages: ChatMessage[];
  operator: Operator;
  onSendMessage: (text: string, attachment?: File) => void;
  menuButtons: string[];
  sessionState: string;
  uiTexts: Record<string, string>;
  closeLabel: string;
  newDialogLabel: string;
  onMenuClick: (label: string) => void;
  onFeedbackClick: (label: string) => void;
  onCloseSession: () => void;
  onNewDialog: () => void;
}

const ChatPopup = ({ isOpen, onClose, messages, operator, onSendMessage, menuButtons, sessionState, uiTexts, closeLabel, newDialogLabel, onMenuClick, onFeedbackClick, onCloseSession, onNewDialog }: ChatPopupProps) => {
  const [inputValue, setInputValue] = useState('');
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const [isInitialLoad, setIsInitialLoad] = useState(true);
  const shouldAutoScrollRef = useRef(true);
  const previousMessageKeyRef = useRef<string | null>(null);

  const isNearBottom = () => {
    const container = messagesContainerRef.current;
    if (!container) return true;
    return container.scrollHeight - container.scrollTop - container.clientHeight < 80;
  };

  const scrollToBottom = (behavior: ScrollBehavior) => {
    const container = messagesContainerRef.current;
    if (!container) return;
    container.scrollTo({
      top: container.scrollHeight,
      behavior,
    });
  };

  const handleMessagesScroll = () => {
    shouldAutoScrollRef.current = isNearBottom();
  };

  const handleMediaReady = () => {
    if (!isOpen || !shouldAutoScrollRef.current) return;
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        scrollToBottom('auto');
      });
    });
  };

  useLayoutEffect(() => {
    if (!isOpen) return;
    const latestMessage = messages[messages.length - 1];
    const latestMessageKey = latestMessage ? `${latestMessage.id}:${String(latestMessage.timestamp)}` : null;
    const isNewMessage = latestMessageKey !== previousMessageKeyRef.current;
    previousMessageKeyRef.current = latestMessageKey;

    if (!isNewMessage) return;
    if (!shouldAutoScrollRef.current && operator.status !== 'typing') return;

    scrollToBottom(isInitialLoad ? 'auto' : 'smooth');
  }, [isOpen, messages, operator.status, isInitialLoad]);

  useEffect(() => {
    if (messages.length > 0) {
      setIsInitialLoad(false);
    }
  }, [messages]);

  useEffect(() => {
    if (isOpen) {
      const timeoutId = window.setTimeout(() => {
        inputRef.current?.focus();
        scrollToBottom('auto');
        shouldAutoScrollRef.current = true;
      }, 150);
      return () => clearTimeout(timeoutId);
    }
  }, [isOpen]);

  const handleSend = () => {
    const trimmed = inputValue.trim();
    if (!trimmed) return;
    onSendMessage(trimmed);
    setInputValue('');
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      onSendMessage(`📎 ${file.name}`, file);
      e.target.value = '';
    }
  };

  return (
    <AnimatePresence>
      {isOpen && (
        <motion.div
          initial={{ opacity: 0, y: 20, scale: 0.9 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{ opacity: 0, y: 20, scale: 0.9 }}
          transition={{ type: 'spring', stiffness: 350, damping: 25 }}
          className="fixed bottom-24 right-4 sm:right-6 w-[360px] max-w-[calc(100vw-2rem)] h-[520px] max-h-[calc(100vh-8rem)] rounded-2xl border border-border overflow-hidden flex flex-col z-50"
          style={{
            background: 'linear-gradient(180deg, hsl(0,0%,11%) 0%, hsl(0,0%,7%) 100%)',
            boxShadow: '0 25px 60px rgba(0,0,0,0.5), 0 0 40px hsla(0,72%,51%,0.1)',
          }}
        >
          {/* Header */}
          <div className="relative px-4 py-3 border-b border-border flex items-center gap-3 shrink-0">
            {/* Decorative top line */}
            <div className="absolute top-0 left-0 right-0 h-[2px] bg-gradient-to-r from-transparent via-primary to-transparent opacity-60" />

            <div className="relative">
              <div className="w-10 h-10 rounded-full bg-primary/20 border-2 border-primary/40 flex items-center justify-center text-sm font-bold text-primary">
                A
              </div>
              <motion.div
                className="absolute -bottom-0.5 -right-0.5 w-3.5 h-3.5 rounded-full bg-casino-online border-2 border-background"
                animate={{ scale: [1, 1.2, 1] }}
                transition={{ duration: 2, repeat: Infinity }}
              />
            </div>

            <div className="flex-1 min-w-0">
              <p className="text-sm font-semibold text-foreground">{operator.name}</p>
              <p className="text-xs text-muted-foreground flex items-center gap-1">
                {operator.status === 'typing' ? (
                  <motion.span
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    className="text-primary"
                  >
                    печатает...
                  </motion.span>
                ) : (
                  <>
                    <span className="w-1.5 h-1.5 rounded-full bg-casino-online inline-block" />
                    в сети
                  </>
                )}
              </p>
            </div>

            <motion.button
              whileHover={{ scale: 1.1, rotate: 90 }}
              whileTap={{ scale: 0.9 }}
              onClick={onClose}
              className="w-8 h-8 rounded-full flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
            >
              <X size={18} />
            </motion.button>
          </div>

          {/* Messages */}
          <div
            ref={messagesContainerRef}
            onScroll={handleMessagesScroll}
            className="flex-1 overflow-y-auto overflow-x-hidden py-3 chat-scrollbar"
            style={{ minHeight: 0 }}
          >
            {isInitialLoad ? (
              <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
                Загрузка...
              </div>
            ) : (
              <div className="space-y-3 pr-1 min-w-0">
                {messages.map((msg) => (
                  <ChatBubble
                    key={`${msg.id}-${String(msg.timestamp)}`}
                    message={msg}
                    onMediaReady={handleMediaReady}
                  />
                ))}
                {operator.status === 'typing' && <TypingIndicator />}
              </div>
            )}
          </div>

          {/* Input */}
          <div className="px-3 py-3 border-t border-border shrink-0">
            <div className="flex flex-wrap gap-2 mb-3 min-w-0">
              {sessionState === 'in_conversation' ? (
                <motion.button
                  whileHover={{ scale: 1.05 }}
                  whileTap={{ scale: 0.95 }}
                  onClick={onCloseSession}
                  className="px-3 py-1.5 rounded-full bg-primary/20 text-primary text-xs border border-primary/30"
                >
                  {closeLabel}
                </motion.button>
              ) : (
                <>
                  {sessionState === 'choosing_feedback_type' ? (
                    <>
                      <motion.button
                        whileHover={{ scale: 1.05 }}
                        whileTap={{ scale: 0.95 }}
                        onClick={() => onFeedbackClick(uiTexts.feedback_positive || '😊 Положительный')}
                        className="px-3 py-1.5 rounded-full bg-accent text-foreground text-xs border border-border"
                      >
                        {uiTexts.feedback_positive || '😊 Положительный'}
                      </motion.button>
                      <motion.button
                        whileHover={{ scale: 1.05 }}
                        whileTap={{ scale: 0.95 }}
                        onClick={() => onFeedbackClick(uiTexts.feedback_negative || '😞 Отрицательный')}
                        className="px-3 py-1.5 rounded-full bg-accent text-foreground text-xs border border-border"
                      >
                        {uiTexts.feedback_negative || '😞 Отрицательный'}
                      </motion.button>
                    </>
                  ) : (
                    menuButtons.map((btn) => (
                      <motion.button
                        key={btn}
                        whileHover={{ scale: 1.05 }}
                        whileTap={{ scale: 0.95 }}
                        onClick={() => onMenuClick(btn)}
                        className="px-3 py-1.5 rounded-full bg-accent text-foreground text-xs border border-border"
                      >
                        {btn}
                      </motion.button>
                    ))
                  )}
                  <motion.button
                    whileHover={{ scale: 1.05 }}
                    whileTap={{ scale: 0.95 }}
                    onClick={onNewDialog}
                    className="px-3 py-1.5 rounded-full bg-muted text-muted-foreground text-xs border border-border"
                  >
                    {newDialogLabel}
                  </motion.button>
                </>
              )}
            </div>
            <div className="flex items-center gap-2 bg-input rounded-xl px-3 py-2.5 focus-within:ring-1 focus-within:ring-primary/50 transition-shadow min-h-[44px] min-w-0">
              <motion.button
                whileHover={{ scale: 1.15 }}
                whileTap={{ scale: 0.9 }}
                onClick={() => fileInputRef.current?.click()}
                className="text-muted-foreground hover:text-primary transition-colors shrink-0 flex items-center justify-center"
                type="button"
              >
                <Paperclip size={18} />
              </motion.button>
              <input
                ref={inputRef}
                type="text"
                value={inputValue}
                onChange={(e) => setInputValue(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Введите сообщение..."
                className="flex-1 bg-transparent text-sm text-foreground placeholder:text-muted-foreground outline-none min-w-0"
              />
              <motion.button
                whileHover={{ scale: 1.15 }}
                whileTap={{ scale: 0.9 }}
                onClick={handleSend}
                disabled={!inputValue.trim()}
                className="text-primary disabled:text-muted-foreground transition-colors shrink-0 flex items-center justify-center"
                type="button"
              >
                <Send size={18} />
              </motion.button>
            </div>
            <input
              ref={fileInputRef}
              type="file"
              className="hidden"
              onChange={handleFileSelect}
              accept="image/*,.pdf,.doc,.docx,.txt"
            />
            <p className="text-[10px] text-muted-foreground text-center mt-2 opacity-50">
              Powered by Casino Support
            </p>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
};

export default ChatPopup;
