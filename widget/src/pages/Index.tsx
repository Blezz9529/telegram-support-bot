import { motion } from 'framer-motion';
import { Spade } from 'lucide-react';
import ChatWidget from '@/components/chat/ChatWidget';

const Index = () => {
  return (
    <div className="min-h-screen bg-background relative overflow-hidden">
      {/* Ambient background glow */}
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[600px] rounded-full opacity-[0.03]"
        style={{ background: 'radial-gradient(circle, hsl(0,72%,51%) 0%, transparent 70%)' }}
      />

      {/* Content */}
      <div className="relative z-10 flex flex-col items-center justify-center min-h-screen px-4">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6 }}
          className="text-center"
        >
          <motion.div
            animate={{ rotate: 360 }}
            transition={{ duration: 20, repeat: Infinity, ease: 'linear' }}
            className="inline-block mb-6"
          >
            <Spade size={48} className="text-primary" />
          </motion.div>

          <h1 className="text-4xl sm:text-5xl font-black tracking-tight mb-3">
            <span className="text-gradient-red">ROYAL</span>{' '}
            <span className="text-foreground">CASINO</span>
          </h1>
          <p className="text-muted-foreground text-lg max-w-md mx-auto">
            Ваша удача начинается здесь. Нужна помощь? Нажмите на чат поддержки.
          </p>
        </motion.div>
      </div>

      {/* Chat Widget */}
      <ChatWidget />
    </div>
  );
};

export default Index;
