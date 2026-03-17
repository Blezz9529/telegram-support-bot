import { cleanupExpiredMedia, clearSessionMedia, getIncomingMedia, saveIncomingMedia } from '@/lib/mediaCache';

// Widget API Configuration (default to same-origin)
const ENV_API_URL = import.meta.env.VITE_WIDGET_API_URL;
const FALLBACK_ORIGIN =
  typeof window !== 'undefined' ? window.location.origin : 'http://localhost:8000';

function normalizeApiBaseUrl(value?: string): string {
  if (!value) return FALLBACK_ORIGIN;
  try {
    const url = new URL(value, FALLBACK_ORIGIN);
    // If someone provided a host alias like "api" or a relative URL, keep same-origin.
    if (url.hostname === 'api') return FALLBACK_ORIGIN;
    return url.origin;
  } catch {
    return FALLBACK_ORIGIN;
  }
}

const API_BASE_URL = normalizeApiBaseUrl(ENV_API_URL);

export interface ChatMessage {
  id: string | number;
  text: string;
  sender: 'user' | 'operator';
  timestamp: Date | string;
  attachment?: {
    name: string;
    type: string;
    url?: string;
    isPlaceholder?: boolean;
    kind?: 'image' | 'animation';
  };
}

export interface Operator {
  name: string;
  avatar: string;
  status: 'online' | 'typing';
}

export interface WidgetUiConfig {
  menu_buttons: string[];
  texts: Record<string, string>;
  close_button_label: string;
  new_dialog_label: string;
  ttl_hours: number;
}

export interface WidgetSessionState {
  state: string;
  theme?: string;
  feedback_type?: string;
  is_blocked: boolean;
  last_activity?: string;
  ttl_hours: number;
}

// Session management
let sessionId: string | null = null;
let siteUserId: string | null = null;

function getSiteUserId(): string | null {
  if (siteUserId) return siteUserId;
  if (typeof window === 'undefined') return null;

  const url = new URL(window.location.href);
  const fromQuery = url.searchParams.get('site_user_id');
  if (fromQuery) {
    localStorage.setItem('widget_site_user_id', fromQuery);
    siteUserId = fromQuery;
    return siteUserId;
  }

  const stored = localStorage.getItem('widget_site_user_id');
  if (stored) {
    siteUserId = stored;
    return siteUserId;
  }

  const generated =
    typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
      ? crypto.randomUUID()
      : `site_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
  localStorage.setItem('widget_site_user_id', generated);
  siteUserId = generated;
  return siteUserId;
}

export function getSessionId(): string | null {
  if (!sessionId) {
    sessionId = localStorage.getItem('widget_session_id');
  }
  return sessionId;
}

export function setSessionId(id: string): void {
  sessionId = id;
  localStorage.setItem('widget_session_id', id);
}

function mapAttachment(message: any) {
  if (!message) return undefined;

  if (message.attachment && typeof message.attachment === 'object') {
    return {
      name: message.attachment.name || message.attachment_name || 'image',
      type: message.attachment.type || message.attachment_type || 'image/jpeg',
      url: message.attachment.url || undefined,
      isPlaceholder: !message.attachment.url,
      kind:
        message.attachment.kind ||
        ((message.attachment.type || message.attachment_type) === 'video/mp4' ? 'animation' : 'image')
    };
  }

  if (!message.attachment_type && !message.attachment_name && !message.attachment_url) {
    return undefined;
  }

  return {
    name: message.attachment_name || 'image',
    type: message.attachment_type || 'image/jpeg',
    url: message.attachment_url || undefined,
    isPlaceholder: !message.attachment_url,
    kind: message.attachment_type === 'video/mp4' ? 'animation' : 'image'
  };
}

async function dataUrlToObjectUrl(dataUrl: string): Promise<string> {
  const response = await fetch(dataUrl);
  const blob = await response.blob();
  return URL.createObjectURL(blob);
}

async function materializeAttachmentUrl(message: ChatMessage): Promise<ChatMessage> {
  if (!message.attachment?.url || !message.attachment.url.startsWith('data:')) {
    return message;
  }

  try {
    const objectUrl = await dataUrlToObjectUrl(message.attachment.url);
    return {
      ...message,
      attachment: {
        ...message.attachment,
        url: objectUrl
      }
    };
  } catch (error) {
    console.error('[Widget API] Failed to materialize media URL:', error);
    return message;
  }
}

export function getMessageCacheKey(message: any): string {
  if (message?.id && message.id !== 0) {
    return String(message.id);
  }
  return `${message?.sender || 'operator'}:${message?.timestamp || Date.now()}:${message?.attachment_name || message?.text || 'message'}`;
}

function mapChatMessage(message: any): ChatMessage {
  const fallbackId = getMessageCacheKey(message);
  return {
    ...message,
    id: fallbackId,
    timestamp: message.timestamp,
    attachment: mapAttachment(message)
  };
}

async function hydrateMessagesWithCache(
  session_id: string,
  messages: ChatMessage[]
): Promise<ChatMessage[]> {
  const hydrated = await Promise.all(
    messages.map(async (message) => {
      if (message.sender === 'user' || !message.attachment?.isPlaceholder) {
        return message;
      }
      const cached = await getIncomingMedia(session_id, getMessageCacheKey(message));
      if (!cached) {
        return message;
      }
      const hydratedMessage = {
        ...message,
        attachment: {
          ...message.attachment,
          ...cached,
          isPlaceholder: false
        }
      };
      return materializeAttachmentUrl(hydratedMessage);
    })
  );
  return hydrated;
}

// API Functions
export async function initSession(
  user_id?: number,
  username?: string,
  full_name?: string
): Promise<{ session_id: string; messages: ChatMessage[] }> {
  await cleanupExpiredMedia();
  const response = await fetch(`${API_BASE_URL}/api/widget/session/init`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id, username, full_name, site_user_id: getSiteUserId() })
  });
  
  if (response.status === 403) {
    const err: any = new Error('Session blocked');
    err.code = 'BLOCKED';
    throw err;
  }

  if (!response.ok) {
    throw new Error('Failed to initialize session');
  }
  
  const data = await response.json();
  setSessionId(data.session_id);
  const mappedMessages = data.messages.map((m: any) => mapChatMessage(m));
  
  return {
    session_id: data.session_id,
    messages: await hydrateMessagesWithCache(data.session_id, mappedMessages)
  };
}

export async function fetchMessages(): Promise<ChatMessage[]> {
  const session_id = getSessionId();
  if (!session_id) return [];
  await cleanupExpiredMedia();
  
  try {
    const response = await fetch(`${API_BASE_URL}/api/widget/messages/${session_id}`);
    if (response.status === 403) {
      const err: any = new Error('Session blocked');
      err.code = 'BLOCKED';
      throw err;
    }
    if (!response.ok) return [];
    
    const messages = await response.json();
    const mappedMessages = messages.map((m: any) => mapChatMessage(m));
    if (mappedMessages.length === 0) {
      await clearSessionMedia(session_id);
      return [];
    }
    return await hydrateMessagesWithCache(session_id, mappedMessages);
  } catch (error) {
    console.error('[Widget API] Error fetching messages:', error);
    return [];
  }
}

export async function sendMessageToTelegram(
  message: string,
  attachment?: File
): Promise<void> {
  const session_id = getSessionId();
  if (!session_id) {
    console.error('[Widget API] No session ID');
    return;
  }
  
  try {
    const payload: any = {
      session_id,
      text: message,
      site_user_id: getSiteUserId()
    };
    
    if (attachment) {
      // Convert file to base64
      const base64 = await fileToBase64(attachment);
      payload.attachment = base64;
      payload.attachment_type = attachment.type;
    }
    
    const response = await fetch(`${API_BASE_URL}/api/widget/message`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    
    if (response.status === 403) {
      const err: any = new Error('Session blocked');
      err.code = 'BLOCKED';
      throw err;
    }
    if (!response.ok) {
      let detail = 'Failed to send message';
      try {
        const errorData = await response.json();
        detail = errorData?.detail || detail;
      } catch {
        // ignore
      }
      throw new Error(detail);
    }
  } catch (error) {
    console.error('[Widget API] Error sending message:', error);
    throw error;
  }
}

export async function saveMessageToDb(message: ChatMessage): Promise<void> {
  // Messages are saved on the backend automatically
  // This function is kept for compatibility
}

export async function getOperatorStatus(): Promise<{ status: string; operator_name: string; typing: boolean }> {
  const session_id = getSessionId();
  if (!session_id) {
    return { status: 'online', operator_name: 'Алекс', typing: false };
  }
  
  try {
    const response = await fetch(`${API_BASE_URL}/api/widget/status/${session_id}`);
    if (!response.ok) {
      return { status: 'online', operator_name: 'Алекс', typing: false };
    }
    return await response.json();
  } catch (error) {
    console.error('[Widget API] Error getting operator status:', error);
    return { status: 'online', operator_name: 'Алекс', typing: false };
  }
}

export async function fetchUiConfig(): Promise<WidgetUiConfig | null> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/widget/ui-config`);
    if (!response.ok) return null;
    return await response.json();
  } catch (error) {
    console.error('[Widget API] Error fetching ui-config:', error);
    return null;
  }
}

export async function fetchSessionState(session_id: string): Promise<any | null> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/widget/session/state/${session_id}`);
    if (!response.ok) return null;
    return await response.json();
  } catch (error) {
    console.error('[Widget API] Error fetching session state:', error);
    return null;
  }
}

export async function selectMenu(session_id: string, menu_label: string): Promise<any> {
  const response = await fetch(`${API_BASE_URL}/api/widget/menu/select`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id, menu_label, site_user_id: getSiteUserId() })
  });
  return await response.json();
}

export async function selectFeedback(session_id: string, label: string): Promise<any> {
  const response = await fetch(`${API_BASE_URL}/api/widget/feedback/select`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id, label })
  });
  return await response.json();
}

export async function closeWidgetSession(session_id: string): Promise<any> {
  const response = await fetch(`${API_BASE_URL}/api/widget/session/close`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id })
  });
  return await response.json();
}

// WebSocket for real-time messages
let ws: WebSocket | null = null;
let onMessageCallback: ((message: ChatMessage) => void) | null = null;
let onTypingCallback: ((typing: boolean) => void) | null = null;
let reconnectAttempts = 0;
const MAX_RECONNECT_ATTEMPTS = 5;
const RECONNECT_DELAY = 2000; // 2 seconds

export function connectWebSocket(
  onMessage: (message: ChatMessage) => void,
  onTyping?: (typing: boolean) => void
): void {
  const session_id = getSessionId();
  if (!session_id) {
    console.error('[Widget WS] No session ID');
    return;
  }
  
  try {
    const wsUrl = `${API_BASE_URL.replace('http', 'ws')}/api/widget/ws/${session_id}`;
    console.log('[Widget WS] Connecting to:', wsUrl);
    
    ws = new WebSocket(wsUrl);
    
    ws.onopen = () => {
      console.log('[Widget WS] Connected');
      reconnectAttempts = 0; // Reset on successful connection
    };
    
    ws.onmessage = async (event) => {
      try {
        const data = JSON.parse(event.data);
        console.log('[Widget WS] Message received:', data);
        if ((data.type === 'message' || data.type === 'image' || data.type === 'animation') && data.data) {
          const ts = data.data.timestamp;
          let mappedMessage = mapChatMessage({
            ...data.data,
            timestamp: ts ?? new Date().toISOString()
          });
          if (
            session_id &&
            mappedMessage.sender !== 'user' &&
            mappedMessage.attachment?.url &&
            (data.type === 'image' || data.type === 'animation')
          ) {
            void saveIncomingMedia(session_id, getMessageCacheKey(mappedMessage), {
              name: mappedMessage.attachment.name,
              type: mappedMessage.attachment.type,
              url: mappedMessage.attachment.url,
              kind: mappedMessage.attachment.kind
            });
          }
          mappedMessage = await materializeAttachmentUrl(mappedMessage);
          onMessage(mappedMessage);
        } else if (data.type === 'typing' && data.data) {
          console.log('[Widget WS] Operator typing:', data.data.typing);
          onTyping?.(Boolean(data.data.typing));
        }
      } catch (error) {
        console.error('[Widget WS] Error parsing message:', error);
      }
    };
    
    ws.onclose = (event) => {
      console.log('[Widget WS] Disconnected:', event.code, event.reason);
      
      // Auto-reconnect with exponential backoff
      if (reconnectAttempts < MAX_RECONNECT_ATTEMPTS) {
        const delay = RECONNECT_DELAY * Math.pow(2, reconnectAttempts);
        console.log(`[Widget WS] Reconnecting in ${delay}ms (attempt ${reconnectAttempts + 1}/${MAX_RECONNECT_ATTEMPTS})`);
        reconnectAttempts++;
        setTimeout(() => connectWebSocket(onMessage, onTyping), delay);
      } else {
        console.error('[Widget WS] Max reconnect attempts reached');
      }
    };
    
    ws.onerror = (error) => {
      console.error('[Widget WS] Error:', error);
    };
    
    onMessageCallback = onMessage;
    onTypingCallback = onTyping || null;
  } catch (error) {
    console.error('[Widget WS] Failed to connect:', error);
    // Try to reconnect after 5 seconds
    setTimeout(() => connectWebSocket(onMessage, onTyping), 5000);
  }
}

export function disconnectWebSocket(): void {
  if (ws) {
    console.log('[Widget WS] Manual disconnect');
    ws.close();
    ws = null;
  }
  reconnectAttempts = 0;
}

// Export reconnect function for manual use
export function reconnectWebSocket(): void {
  if (onMessageCallback) {
    disconnectWebSocket();
    setTimeout(() => connectWebSocket(onMessageCallback, onTypingCallback || undefined), 1000);
  }
}

export { clearSessionMedia };

// Helper: File to Base64
function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

// Echo simulator with delay (fallback if API is unavailable)
export function simulateOperatorReply(userMessage: string): Promise<ChatMessage> {
  return new Promise((resolve) => {
    setTimeout(() => {
      resolve({
        id: crypto.randomUUID(),
        text: userMessage,
        sender: 'operator',
        timestamp: new Date(),
      });
    }, 5000);
  });
}
