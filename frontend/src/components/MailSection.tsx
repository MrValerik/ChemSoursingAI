import { useEffect, useMemo, useRef, useState } from "react";
import { api, userErrorMessage } from "../api/client";
import type {
  EmailSyncRead,
  MailboxFolder,
  MailboxMessageRead,
  MailboxThreadRead,
  MailboxThreadDetailRead,
} from "../api/types";
import { useAuth } from "../auth/AuthContext";

const FOLDERS: Array<{ key: MailboxFolder; label: string }> = [
  { key: "all", label: "Все письма" },
  { key: "inbox", label: "Входящие" },
  { key: "sent", label: "Отправленные" },
  { key: "unresolved", label: "Неопределённые" },
];

const MAIL_STATUS: Record<string, string> = {
  received: "Получено", sent: "Отправлено", draft: "Черновик",
  sending: "Отправляется", queued: "В очереди", delivered: "Доставлено",
  error: "Ошибка", delivery_error: "Ошибка отправки",
};

const shortText = (value: string | null, max = 120) => {
  const text = (value || "").replace(/\s+/g, " ").trim();
  return text.length > max ? `${text.slice(0, max)}…` : text;
};

const formatDate = (value: string) =>
  new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));

function syncNotice(result: EmailSyncRead) {
  if (result.errors.length) {
    return `Проверка завершена с ошибками: ${result.errors.join("; ")}`;
  }
  return (
    `Почта обновлена: получено ${result.fetched}, сохранено ${result.processed}, ` +
    `неопределённых ${result.unmatched}.`
  );
}

function MailThreadMessage({ message, expanded, onReply, onDownload, downloadBusy }: {
  message: MailboxMessageRead;
  expanded: boolean;
  onReply?: () => void;
  onDownload: (documentId: number, filename: string) => void;
  downloadBusy: number | null;
}) {
  const [open, setOpen] = useState(expanded);
  return (
    <details className={`mail-thread-message mail-thread-message--${message.direction}`}
      open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>
        <span className="mail-thread-message-heading">
          <strong>{message.subject || "Без темы"}</strong>
          <time>{formatDate(message.message_at)}</time>
        </span>
        <span className="mail-list-tags">
          <span>{message.direction === "inbound" ? "Входящее" : "Отправленное"}</span>
          {message.rfq_id && <span>RFQ-{message.rfq_id}</span>}
          {message.is_unresolved && <span className="mail-unresolved">Неопределённый email</span>}
        </span>
        {!open && <span className="mail-list-preview">{shortText(message.body) || "Пустое письмо"}</span>}
      </summary>
      <div className="mail-thread-message-content">
        <header className="mail-reader-header">
          <dl>
            <div><dt>От:</dt><dd>{message.from_address || "—"}</dd></div>
            <div><dt>Кому:</dt><dd>{message.to_address || "—"}</dd></div>
            <div><dt>Статус:</dt><dd>{MAIL_STATUS[message.status || ""] || message.status || "—"}</dd></div>
          </dl>
          {onReply && <button className="secondary" onClick={onReply}>Ответить</button>}
        </header>
        {message.is_unresolved && (
          <div className="mail-unresolved-explanation">
            Письмо не связано с RFQ. Общий email-адрес объединяет историю,
            но сам по себе не подтверждает связь с запросом.
          </div>
        )}
        <div className="mail-body">{message.body || "Письмо не содержит текста."}</div>
        {!!message.attachments?.length && (
          <div className="mail-attachments">
            <strong>Вложения</strong>
            {message.attachments.map((attachment, index) => attachment.document_id ? (
              <button key={`${attachment.filename}-${index}`} className="secondary"
                onClick={() => onDownload(attachment.document_id!, attachment.filename)}
                disabled={downloadBusy === attachment.document_id}>
                {downloadBusy === attachment.document_id ? "Загрузка…" : "Скачать"}
                {` ${attachment.filename} · ${Math.ceil(attachment.size / 1024)} КБ`}
              </button>
            ) : (
              <span key={`${attachment.filename}-${index}`}>
                {attachment.filename} · {Math.ceil(attachment.size / 1024)} КБ
              </span>
            ))}
          </div>
        )}
      </div>
    </details>
  );
}

export default function MailSection() {
  const { user } = useAuth();
  const canWrite = user?.role !== "auditor";
  const [folder, setFolder] = useState<MailboxFolder>("all");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [search, setSearch] = useState("");
  const [items, setItems] = useState<MailboxThreadRead[]>([]);
  const [total, setTotal] = useState(0);
  const [totalMessages, setTotalMessages] = useState(0);
  const [offset, setOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [history, setHistory] = useState<MailboxThreadDetailRead | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [olderLoading, setOlderLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const historyVersion = useRef(0);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [sending, setSending] = useState(false);
  const [downloadBusy, setDownloadBusy] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [composing, setComposing] = useState(false);
  const [replyToId, setReplyToId] = useState<number | null>(null);
  const [toAddress, setToAddress] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const composeRef = useRef<HTMLFormElement>(null);

  useEffect(() => {
    if (composing) composeRef.current?.scrollIntoView({ block: "start", behavior: "smooth" });
  }, [composing, replyToId]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    const timer = window.setTimeout(async () => {
      try {
        const result = await api.listMailboxThreads({
          folder, offset,
          date_from: dateFrom || undefined,
          date_to: dateTo || undefined,
          query: search || undefined,
        });
        if (cancelled) return;
        setItems(result.items);
        setTotal(result.total);
        setTotalMessages(result.total_messages);
        setSelectedKey((current) =>
          result.items.some((item) => item.key === current)
            ? current : result.items[0]?.key ?? null,
        );
        setError(null);
      } catch (caught) {
        if (!cancelled) setError(userErrorMessage(caught));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, 250);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [dateFrom, dateTo, folder, search, offset, refresh]);

  const selected = useMemo(
    () => items.find((item) => item.key === selectedKey) ?? null,
    [items, selectedKey],
  );

  const anchorId = selected?.latest_message.id;
  useEffect(() => {
    const version = ++historyVersion.current;
    setHistory(null);
    setHistoryError(null);
    setOlderLoading(false);
    setHistoryLoading(!!anchorId);
    if (!anchorId) return;
    void api.getMailboxThread(anchorId).then((result) => {
      if (version === historyVersion.current) setHistory(result);
    }).catch((caught) => {
      if (version === historyVersion.current) setHistoryError(userErrorMessage(caught));
    }).finally(() => {
      if (version === historyVersion.current) setHistoryLoading(false);
    });
    return () => { historyVersion.current++; };
  }, [anchorId, refresh]);

  const activeHistory = history?.key === selected?.key ? history : null;
  const loadOlder = async () => {
    if (!anchorId || !activeHistory?.next_before_id || olderLoading) return;
    const version = historyVersion.current;
    setOlderLoading(true);
    setHistoryError(null);
    try {
      const result = await api.getMailboxThread(anchorId, activeHistory.next_before_id);
      if (version !== historyVersion.current) return;
      setHistory((current) => current?.key === result.key ? {
        ...result,
        items: [...result.items, ...current.items],
      } : current);
    } catch (caught) {
      if (version === historyVersion.current) setHistoryError(userErrorMessage(caught));
    } finally {
      if (version === historyVersion.current) setOlderLoading(false);
    }
  };

  const startCompose = (message?: MailboxMessageRead) => {
    if (message) {
      setReplyToId(message.id);
      setToAddress(selected?.correspondent || "");
      const source = message.subject || "";
      setSubject(/^re:/i.test(source) ? source : `Re: ${source}`);
    } else {
      setReplyToId(null);
      setToAddress("");
      setSubject("");
    }
    setBody("");
    setComposing(true);
    setNotice(null);
  };

  const sync = async () => {
    setSyncing(true);
    setNotice(null);
    try {
      const result = await api.syncEmailCommunications(100);
      setNotice(syncNotice(result));
      setRefresh((value) => value + 1);
    } catch (caught) {
      setError(userErrorMessage(caught));
    } finally {
      setSyncing(false);
    }
  };

  const send = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!toAddress.trim() || !subject.trim() || !body.trim()) return;
    if (!window.confirm(`Отправить письмо на ${toAddress.trim()}?`)) return;
    setSending(true);
    try {
      await api.sendMailboxMessage({
        to_address: toAddress.trim(),
        subject: subject.trim(),
        body: body.trim(),
        idempotency_key: crypto.randomUUID(),
        reply_to_message_id: replyToId,
        confirm_external_send: true,
      });
      setComposing(false);
      setNotice("Письмо отправлено и сохранено в общей почте.");
      setFolder("all");
      setDateFrom("");
      setDateTo("");
      setSearch("");
      setOffset(0);
      setSelectedKey(`email:${toAddress.trim().toLowerCase()}`);
      setRefresh((value) => value + 1);
    } catch (caught) {
      setError(userErrorMessage(caught));
    } finally {
      setSending(false);
    }
  };

  const downloadAttachment = async (
    documentId: number,
    filename: string,
  ) => {
    setDownloadBusy(documentId);
    setError(null);
    try {
      const blob = await api.downloadDocument(documentId);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (caught) {
      setError(userErrorMessage(caught));
    } finally {
      setDownloadBusy(null);
    }
  };

  return (
    <section className="mail-page">
      <header className="mail-header">
        <div>
          <h1>Почта</h1>
          <p>Общий служебный ящик. Письма и ответы объединены по email-адресу.</p>
        </div>
        <div className="mail-header-actions">
          {canWrite && (
            <button className="secondary" onClick={() => void sync()} disabled={syncing}>
              {syncing ? "Проверка…" : "Проверить входящие"}
            </button>
          )}
          {canWrite && (
            <button onClick={() => startCompose()}>Написать письмо</button>
          )}
        </div>
      </header>

      <div className="mail-filters" aria-label="Фильтры почты">
        <nav className="mail-folders" aria-label="Папки">
          {FOLDERS.map((item) => (
            <button
              key={item.key}
              className={folder === item.key ? "is-active" : ""}
              onClick={() => { setFolder(item.key); setOffset(0); }}
            >
              {item.label}
            </button>
          ))}
        </nav>
        <label>
          Поиск
          <input
            value={search}
            onChange={(event) => { setSearch(event.target.value); setOffset(0); }}
            placeholder="Адрес, тема или текст"
          />
        </label>
        <label>
          С даты
          <input type="date" value={dateFrom} onChange={(event) => { setDateFrom(event.target.value); setOffset(0); }} />
        </label>
        <label>
          По дату
          <input type="date" value={dateTo} onChange={(event) => { setDateTo(event.target.value); setOffset(0); }} />
        </label>
        {(dateFrom || dateTo || search) && (
          <button
            className="secondary mail-reset"
            onClick={() => {
              setDateFrom("");
              setDateTo("");
              setSearch("");
              setOffset(0);
            }}
          >
            Сбросить
          </button>
        )}
      </div>

      {error && <div className="error-box">{error}</div>}
      {notice && <div className="mail-notice">{notice}</div>}

      {composing && (
        <form ref={composeRef} className="panel mail-compose" onSubmit={(event) => void send(event)}>
          <div className="mail-compose-title">
            <h2>{replyToId ? "Ответить" : "Новое письмо"}</h2>
            <button type="button" className="secondary" onClick={() => setComposing(false)}>
              Закрыть
            </button>
          </div>
          <div className="field">
            <label htmlFor="mail-to">Кому</label>
            <input id="mail-to" type="email" value={toAddress} onChange={(event) => setToAddress(event.target.value)} required />
          </div>
          <div className="field">
            <label htmlFor="mail-subject">Тема</label>
            <input id="mail-subject" value={subject} onChange={(event) => setSubject(event.target.value)} maxLength={998} required />
          </div>
          <div className="field">
            <label htmlFor="mail-body">Сообщение</label>
            <textarea id="mail-body" value={body} onChange={(event) => setBody(event.target.value)} maxLength={12000} required />
          </div>
          <div className="mail-compose-actions">
            <span className="note">Письмо уйдёт с настроенного служебного адреса.</span>
            <button type="submit" disabled={sending}>{sending ? "Отправка…" : "Отправить"}</button>
          </div>
        </form>
      )}

      <div className="mail-workspace">
        <aside className="mail-list" aria-label="Список переписок">
          <div className="mail-list-summary">
            {loading ? "Загрузка…" : `Переписок: ${total} · писем по фильтрам: ${totalMessages}`}
          </div>
          {!loading && items.length === 0 && (
            <div className="mail-empty">По выбранным фильтрам писем нет.</div>
          )}
          {items.map((thread) => {
            const message = thread.latest_message;
            return (
              <button
                key={thread.key}
                className={`mail-list-item${selectedKey === thread.key ? " is-selected" : ""}`}
                onClick={() => setSelectedKey(thread.key)}
                aria-pressed={selectedKey === thread.key}
              >
                <span className="mail-list-topline">
                  <strong>{thread.correspondent || "Адрес не указан"}</strong>
                  <time>{formatDate(message.message_at)}</time>
                </span>
                <span className="mail-list-subject">{message.subject || "Без темы"}</span>
                <span className="mail-list-preview">{shortText(message.body) || "Пустое письмо"}</span>
                <span className="mail-list-tags">
                  <span>Писем: {thread.message_count}</span>
                  {thread.matched_count !== thread.message_count && <span>По фильтрам: {thread.matched_count}</span>}
                  {thread.unresolved_count > 0 && <span className="mail-unresolved">Без RFQ: {thread.unresolved_count}</span>}
                  {thread.rfq_ids.slice(0, 3).map((id) => <span key={id}>RFQ-{id}</span>)}
                  {thread.rfq_ids.length > 3 && <span>Ещё RFQ: {thread.rfq_ids.length - 3}</span>}
                </span>
              </button>
            );
          })}
          {total > 50 && (
            <nav className="mail-pagination" aria-label="Страницы переписок">
              <button className="secondary" disabled={loading || offset === 0}
                onClick={() => setOffset((value) => Math.max(0, value - 50))}>Назад</button>
              <span>{offset + 1}–{Math.min(offset + 50, total)} из {total}</span>
              <button className="secondary" disabled={loading || offset + 50 >= total}
                onClick={() => setOffset((value) => value + 50)}>Далее</button>
            </nav>
          )}
        </aside>

        <article className="mail-reader">
          {!selected ? (
            <div className="mail-empty">Выберите переписку слева.</div>
          ) : (
            <>
              <header className="mail-thread-header">
                <h2>{selected.correspondent || "Письмо без адреса"}</h2>
                <p>{selected.correspondent
                  ? "Вся история с этим адресом, включая письма вне выбранных фильтров. Новые письма сверху."
                  : "Адрес не определён, поэтому письмо показано отдельно."}
                  {activeHistory && ` Показано ${activeHistory.items.length} из ${activeHistory.total}.`}</p>
              </header>
              {historyLoading && <div className="mail-empty" role="status">Загрузка переписки…</div>}
              {historyError && (
                <div className="error-box">
                  {historyError}
                  <button className="secondary" onClick={() => setRefresh((value) => value + 1)}>Повторить</button>
                </div>
              )}
              <div className="mail-thread-messages">
                {activeHistory && [...activeHistory.items].reverse().map((message, index) => (
                  <MailThreadMessage key={message.id} message={message}
                    expanded={message.id === anchorId || index === 0}
                    onReply={canWrite && selected.correspondent ? () => startCompose(message) : undefined}
                    onDownload={(id, filename) => void downloadAttachment(id, filename)}
                    downloadBusy={downloadBusy} />
                ))}
              </div>
              {activeHistory?.next_before_id && (
                <button className="secondary mail-load-older" onClick={() => void loadOlder()} disabled={olderLoading}>
                  {olderLoading ? "Загрузка…" : "Показать более ранние письма"}
                </button>
              )}
            </>
          )}
        </article>
      </div>
    </section>
  );
}
