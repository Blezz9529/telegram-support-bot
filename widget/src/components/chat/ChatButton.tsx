import { motion, AnimatePresence } from 'framer-motion';
import { MessageCircle } from 'lucide-react';

interface ChatButtonProps {
  onClick: () => void;
  hasUnread: boolean;
  isOpen: boolean;
}

const ChatButton = ({ onClick, hasUnread, isOpen }: ChatButtonProps) => {
  return (
    <AnimatePresence>
      {!isOpen && (
        <motion.button
          initial={{ scale: 0, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          exit={{ scale: 0, opacity: 0 }}
          transition={{ type: 'spring', stiffness: 400, damping: 20 }}
          onClick={onClick}
          className="fixed bottom-6 right-4 sm:right-6 w-14 h-14 rounded-full bg-primary flex items-center justify-center text-primary-foreground z-50 animate-pulse-glow overflow-visible"
        >
          <div className="chat-button-ring" aria-hidden="true" />
          <motion.div
            animate={{ y: [0, -2, 0] }}
            transition={{ duration: 2.5, repeat: Infinity, ease: 'easeInOut' }}
          >
            <MessageCircle size={24} />
          </motion.div>

          {/* Unread indicator */}
          <AnimatePresence>
            {hasUnread && (
              <motion.div
                initial={{ scale: 0 }}
                animate={{ scale: 1 }}
                exit={{ scale: 0 }}
                className="absolute -top-1 -right-1 w-4 h-4 rounded-full bg-red-500 ring-2 ring-white animate-badge-pop"
              />
            )}
          </AnimatePresence>
        </motion.button>
      )}
    </AnimatePresence>
  );
};

export default ChatButton;
