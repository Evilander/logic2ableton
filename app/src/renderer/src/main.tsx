import React from "react"
import ReactDOM from "react-dom/client"
import App from "./App"
import "./assets/main.css"

// Prevent Electron from navigating to dropped files
document.addEventListener("dragover", (e) => e.preventDefault())
document.addEventListener("drop", (e) => e.preventDefault())

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
