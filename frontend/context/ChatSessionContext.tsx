"use client";

import { createContext, useContext, useState, ReactNode } from "react";

import type { ChromaFact, Message } from "@/lib/types";

// Re-exported so existing imports keep working; the definitions live in
// lib/types.ts, next to the rest of the backend's shapes.
export type { ChromaFact, Message };

interface ChatSessionContextValue {
  messages: Message[];
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>;
  clearMessages: () => void;
}

const ChatSessionContext = createContext<ChatSessionContextValue | null>(null);

export function ChatSessionProvider({ children }: { children: ReactNode }) {
  // Lives at app root — survives navigation between /chat and /dashboard
  const [messages, setMessages] = useState<Message[]>([]);

  const clearMessages = () => setMessages([]);

  return (
    <ChatSessionContext.Provider value={{ messages, setMessages, clearMessages }}>
      {children}
    </ChatSessionContext.Provider>
  );
}

export function useChatSession(): ChatSessionContextValue {
  const ctx = useContext(ChatSessionContext);
  if (!ctx) throw new Error("useChatSession must be used inside ChatSessionProvider");
  return ctx;
}
