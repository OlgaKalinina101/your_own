export {};

declare global {
  interface Window {
    yourOwn: {
      saveApiKey:      (key: string)   => Promise<{ ok: boolean; error?: string }>;
      getApiKey:       ()              => Promise<string | null>;
      saveModel:       (model: string) => Promise<{ ok: boolean; error?: string }>;
      getModel:        ()              => Promise<string | null>;
      saveTemperature: (val: string)   => Promise<{ ok: boolean; error?: string }>;
      getTemperature:  ()              => Promise<string | null>;
      saveTopP:        (val: string)   => Promise<{ ok: boolean; error?: string }>;
      getTopP:         ()              => Promise<string | null>;
      saveSoul:            (text: string)  => Promise<{ ok: boolean; error?: string }>;
      getSoul:             ()              => Promise<string | null>;
      saveHistoryPairs:    (val: string)   => Promise<{ ok: boolean; error?: string }>;
      getHistoryPairs:     ()              => Promise<string | null>;
      saveMemoryCutoffDays: (val: string)   => Promise<{ ok: boolean; error?: string }>;
      getMemoryCutoffDays:  ()              => Promise<string | null>;
      /** Reads data/auth_token.txt off disk — the desktop app's only way in. */
      getBackendAuthToken:  ()              => Promise<string | null>;
    };
  }
}
