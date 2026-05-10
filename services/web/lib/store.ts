import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import type { ChatResponse } from "@/types";

export type TaskStatus = "idle" | "running" | "error";

export interface Message {
  role: "user" | "agent";
  text: string;
  response?: ChatResponse;
  error?: string;
}

interface ChatState {
  messages: Message[];
  taskStatus: TaskStatus;
  addMessage: (msg: Message) => void;
  setTaskStatus: (s: TaskStatus) => void;
  clearMessages: () => void;
}

export const useChatStore = create<ChatState>()(
  persist(
    (set) => ({
      messages: [],
      taskStatus: "idle",
      addMessage: (msg) => set((s) => ({ messages: [...s.messages, msg] })),
      setTaskStatus: (taskStatus) => set({ taskStatus }),
      clearMessages: () => set({ messages: [], taskStatus: "idle" }),
    }),
    {
      name: "chat-store",
      storage: createJSONStorage(() => sessionStorage),
      // only persist data fields, not action functions
      partialize: (s) => ({ messages: s.messages, taskStatus: s.taskStatus }),
      // Prevent auto-hydration at store creation time (which runs on the server
      // in Next.js App Router). We call rehydrate() manually in a useEffect
      // after the component mounts on the client, avoiding the SSR/hydration
      // mismatch that was silently discarding the persisted state.
      skipHydration: true,
    }
  )
);
