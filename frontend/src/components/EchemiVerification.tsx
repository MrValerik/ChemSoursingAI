import { useEffect, useRef, useState } from "react";
import { getEchemiManualStatus, openEchemiManual } from "../api/client";

export default function EchemiVerification({ searchId }: { searchId: number }) {
  const [waiting, setWaiting] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [frame, setFrame] = useState("");
  const [message, setMessage] = useState("");
  const socket = useRef<WebSocket | null>(null);
  const imageUrl = useRef("");
  const lastMove = useRef(0);
  const startedAt = useRef(0);
  const [recording, setRecording] = useState<{ id: string | null; event_count: number; limited: boolean } | null>(null);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const state = await getEchemiManualStatus(searchId);
        if (!alive) return;
        setWaiting(state.waiting); setSeconds(state.remaining_seconds);
        if (!state.waiting) { socket.current?.close(); setMessage(""); }
      } catch { if (alive) setMessage("Не удалось проверить доступность окна. Повторяем подключение…"); }
      finally { if (alive) timer = setTimeout(poll, 2000); }
    }
    void poll();
    return () => {
      alive = false; clearTimeout(timer); socket.current?.close();
      if (imageUrl.current) URL.revokeObjectURL(imageUrl.current);
    };
  }, [searchId]);

  function connect() {
    if (socket.current && socket.current.readyState < WebSocket.CLOSING) return;
    setMessage("Подключение к серверной странице…");
    setRecording(null);
    startedAt.current = performance.now();
    lastMove.current = 0;
    const ws = openEchemiManual(searchId);
    socket.current = ws;
    ws.binaryType = "blob";
    ws.onmessage = event => {
      if (event.data instanceof Blob) {
        const next = URL.createObjectURL(event.data);
        if (imageUrl.current) URL.revokeObjectURL(imageUrl.current);
        imageUrl.current = next; setFrame(next); setConnected(true); setMessage("");
      } else {
        const data = JSON.parse(event.data);
        if (data.type === "finished") { setWaiting(false); ws.close(); }
        else if (data.type === "recording") setRecording(data);
      }
    };
    ws.onclose = () => {
      setConnected(false); setFrame("");
      setRecording(null);
      setMessage("Окно закрыто. Если проверка ещё нужна, подключитесь снова. Другое открытое окно может занимать управление.");
    };
  }

  function pointer(event: React.PointerEvent<HTMLImageElement>, type: string) {
    event.preventDefault();
    if (!event.isPrimary || (type === "down" && event.button !== 0)) return;
    if (!connected || socket.current?.readyState !== WebSocket.OPEN) return;
    if (type === "move" && performance.now() - lastMove.current < 30) return;
    lastMove.current = performance.now();
    if (type === "down") event.currentTarget.setPointerCapture(event.pointerId);
    const bounds = event.currentTarget.getBoundingClientRect();
    socket.current.send(JSON.stringify({type,
      t_ms: performance.now() - startedAt.current,
      x: Math.max(0, Math.min(1279, (event.clientX - bounds.left) * 1280 / bounds.width)),
      y: Math.max(0, Math.min(899, (event.clientY - bounds.top) * 900 / bounds.height)),
    }));
    if (type === "up" && event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }

  if (!waiting) return message ? <p role="status">{message}</p> : null;
  return <div className="echemi-verification">
    <h3>Нужна ручная проверка Echemi</h3>
    <p>Откройте окно и перетащите ползунок на изображении. После успешной проверки поиск продолжится автоматически. Осталось {seconds} с.</p>
    {!connected && <>
      <p>Во время проверки сохраняются движения и нажатия мыши в этом окне. Запись доступна в истории поиска.</p>
      <button onClick={connect}>Решить капчу самостоятельно</button>
    </>}
    {connected && <div className="echemi-recording-status" role="status">
      <span className={recording?.limited ? "" : "echemi-recording-dot"} aria-hidden="true" />
      <span>{!recording ? "Получаем состояние записи…" : recording.limited
        ? "Лимит записи достигнут. Ручная проверка продолжает работать."
        : `Идёт запись мыши · ${recording.event_count} событий`}</span>
      <button onClick={() => socket.current?.close()}>Закрыть окно и остановить запись</button>
    </div>}
    {message && <p role="status">{message}</p>}
    {frame && <img src={frame} alt="Серверная страница Echemi: пройдите проверку мышью" draggable={false}
      style={{width:"100%", maxWidth:1280, touchAction:"none", userSelect:"none"}}
      onContextMenu={e=>e.preventDefault()}
      onPointerDown={e=>pointer(e,"down")} onPointerMove={e=>pointer(e,"move")}
      onPointerUp={e=>pointer(e,"up")} onPointerCancel={e=>pointer(e,"up")} />}
  </div>;
}
