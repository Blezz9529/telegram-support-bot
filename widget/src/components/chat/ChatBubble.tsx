import { motion } from 'framer-motion';
import { ChatMessage } from '@/lib/chatService';
import { Image as ImageIcon } from 'lucide-react';

interface ChatBubbleProps {
  message: ChatMessage;
  onMediaReady?: () => void;
}

const ChatBubble = ({ message, onMediaReady }: ChatBubbleProps) => {
  const isUser = message.sender === 'user';
  const date = message.timestamp ? new Date(message.timestamp) : null;
  const safeDate = date && !Number.isNaN(date.getTime()) ? date : new Date();
  const time = safeDate.toLocaleTimeString('ru-RU', {
    timeZone: 'Europe/Moscow',
    hour: '2-digit',
    minute: '2-digit'
  });
  const hasInlineImage = Boolean(
    message.attachment?.url &&
    message.attachment?.type?.startsWith('image/') &&
    !message.attachment?.isPlaceholder
  );
  const hasInlineAnimation = Boolean(
    message.attachment?.url &&
    message.attachment?.kind === 'animation' &&
    !message.attachment?.isPlaceholder
  );
  const hasPlaceholder = Boolean(message.attachment && !hasInlineImage && !hasInlineAnimation);
  const hasText = Boolean(message.text?.trim());

  return (
    <motion.div
      initial={{ opacity: 0, y: 12, scale: 0.95 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ type: 'spring', stiffness: 400, damping: 25 }}
      className={`flex items-end gap-2 px-4 ${isUser ? 'flex-row-reverse' : ''}`}
    >
      {!isUser && (
        <div className="w-7 h-7 rounded-full bg-primary/20 border border-primary/30 flex items-center justify-center text-[10px] font-bold text-primary shrink-0">
          A
        </div>
      )}
      <div
        className={`max-w-[75%] min-w-0 rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${
          isUser
            ? 'bg-primary text-primary-foreground rounded-br-md'
            : 'bg-secondary text-secondary-foreground rounded-bl-md'
        }`}
      >
        {hasInlineImage && (
          <a
            href={message.attachment?.url}
            target="_blank"
            rel="noopener noreferrer"
            className="mb-2 block w-full overflow-hidden rounded-xl border border-white/10 bg-black/10 transition-opacity hover:opacity-90 cursor-zoom-in"
          >
            <img
              src={message.attachment?.url}
              alt={message.attachment?.name || 'Изображение'}
              className="max-h-56 w-full object-cover"
              onLoad={onMediaReady}
            />
          </a>
        )}
        {hasInlineAnimation && (
          <a
            href={message.attachment?.url}
            target="_blank"
            rel="noopener noreferrer"
            className="mb-2 block w-full overflow-hidden rounded-xl border border-white/10 bg-black/10 transition-opacity hover:opacity-90 cursor-zoom-in"
          >
            <video
              src={message.attachment?.url}
              autoPlay
              muted
              loop
              playsInline
              preload="metadata"
              className="max-h-56 w-full object-cover"
              onLoadedData={onMediaReady}
            />
          </a>
        )}
        {hasPlaceholder && (
          <div className="mb-2 flex items-center gap-2 rounded-xl border border-white/10 bg-black/10 px-3 py-2 text-xs opacity-90">
            <ImageIcon size={14} className="shrink-0" />
            <span className="truncate">{message.attachment?.name}</span>
          </div>
        )}
        {hasText && (
          // Для длинных «токеноподобных» строк (без пробелов, например base64/идентификаторы)
          // рендерим в одну строку с троеточием. Обычный текст оставляем переносимым.
          (() => {
            const t = message.text || '';
            const looksLikeToken = t.length > 48 && !/\s/.test(t);
            return (
              <p className={
                looksLikeToken
                  ? 'block max-w-full truncate'
                  : 'whitespace-pre-wrap break-words'
              }>
                {t}
              </p>
            );
          })()
        )}
        <p className={`text-[10px] mt-1 ${isUser ? 'text-primary-foreground/60' : 'text-muted-foreground'}`}>
          {time}
        </p>
      </div>
    </motion.div>
  );
};

export default ChatBubble;
