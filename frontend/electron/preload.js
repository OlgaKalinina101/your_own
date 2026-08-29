const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("yourOwn", {
  saveApiKey:     (key)   => ipcRenderer.invoke("save-api-key", key),
  getApiKey:      ()      => ipcRenderer.invoke("get-api-key"),
  saveModel:      (model) => ipcRenderer.invoke("save-model", model),
  getModel:       ()      => ipcRenderer.invoke("get-model"),
  saveTemperature:(val)   => ipcRenderer.invoke("save-temperature", val),
  getTemperature: ()      => ipcRenderer.invoke("get-temperature"),
  saveTopP:       (val)   => ipcRenderer.invoke("save-top-p", val),
  getTopP:        ()      => ipcRenderer.invoke("get-top-p"),
  saveSoul:            (text) => ipcRenderer.invoke("save-soul", text),
  getSoul:             ()     => ipcRenderer.invoke("get-soul"),
  saveHistoryPairs:    (val)  => ipcRenderer.invoke("save-history-pairs", val),
  getHistoryPairs:     ()     => ipcRenderer.invoke("get-history-pairs"),
  saveMemoryCutoffDays: (val)  => ipcRenderer.invoke("save-memory-cutoff-days", val),
  getMemoryCutoffDays:  ()     => ipcRenderer.invoke("get-memory-cutoff-days"),
  getBackendAuthToken:  ()     => ipcRenderer.invoke("get-backend-auth-token"),
});
