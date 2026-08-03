import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
// Шрифты лежат в проекте, а не берутся из системы: иначе вид зависит от того,
// что установлено на машине. Оба начертания покрывают кириллицу; браузер
// скачает только нужный поддиапазон — за это отвечает unicode-range.
import "@fontsource-variable/inter/wght.css";
import "@fontsource/source-serif-4/600.css";
import "@fontsource/source-serif-4/cyrillic-600.css";
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
