import { create } from "zustand";

interface AuthState {
  /** The backend answered 401: show the token form instead of the app. */
  needsToken: boolean;
  setNeedsToken: (value: boolean) => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  needsToken: false,
  setNeedsToken: (needsToken) => set({ needsToken }),
}));
