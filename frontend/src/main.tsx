import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./styles.css";
import "./ui.css";

// Настоящие пути, а не хэш: nginx отдаёт index.html на неизвестные адреса
// (frontend/nginx.conf), поэтому глубокая ссылка открывается напрямую.
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);
