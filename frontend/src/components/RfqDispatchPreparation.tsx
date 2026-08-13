import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { RecipientRead, RFQRead } from "../api/types";
import { Input, Textarea } from "./ui";

const STATUS_LABELS: Record<string, string> = {
  queued: "ожидает отправки",
  sent: "отправлено",
  error: "ошибка",
};

export default function RfqDispatchPreparation({
  rfq,
  readOnly,
  onGoToSuppliers,
  onChanged,
}: {
  rfq: RFQRead;
  readOnly: boolean;
  onGoToSuppliers: () => void;
  onChanged: () => void;
}) {
  const [recipients, setRecipients] = useState<RecipientRead[]>([]);
  const [reviewed, setReviewed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [translation, setTranslation] = useState<string | null>(null);
  const [translationVisible, setTranslationVisible] = useState(false);
  const [translationBusy, setTranslationBusy] = useState(false);
  const [savedSubject, setSavedSubject] = useState(
    rfq.rfq_subject ?? "Request for quotation",
  );
  const [savedBody, setSavedBody] = useState(
    rfq.rfq_body ?? "Текст RFQ временно недоступен.",
  );
  const [draftSubject, setDraftSubject] = useState(savedSubject);
  const [draftBody, setDraftBody] = useState(savedBody);
  const [customized, setCustomized] = useState(rfq.rfq_is_customized);

  const queued = useMemo(
    () => recipients.filter((item) => item.status === "queued"),
    [recipients],
  );
  const queueKey = queued.map((item) => `${item.id}:${item.updated_at}`).join("|");

  const load = async () => {
    try {
      setRecipients(await api.listRecipients(rfq.id));
      setError(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rfq.id]);

  useEffect(() => {
    const subject = rfq.rfq_subject ?? "Request for quotation";
    const body = rfq.rfq_body ?? "Текст RFQ временно недоступен.";
    setPreviewOpen(false);
    setSavedSubject(subject);
    setSavedBody(body);
    setDraftSubject(subject);
    setDraftBody(body);
    setCustomized(rfq.rfq_is_customized);
    setTranslation(null);
    setTranslationVisible(false);
  }, [rfq.id, rfq.rfq_subject, rfq.rfq_body, rfq.rfq_is_customized]);

  useEffect(() => {
    // Изменился список или статус получателей — RFQ нужно проверить заново.
    setReviewed(false);
  }, [queueKey]);

  const removeRecipient = async (recipient: RecipientRead) => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await api.removeRecipient(rfq.id, recipient.id);
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  const beginEditing = () => {
    setDraftSubject(savedSubject);
    setDraftBody(savedBody);
    setEditing(true);
    setReviewed(false);
    setError(null);
    setNotice(null);
    setTranslationVisible(false);
  };

  const cancelEditing = () => {
    setDraftSubject(savedSubject);
    setDraftBody(savedBody);
    setEditing(false);
    setError(null);
  };

  const applyDraft = (updated: RFQRead) => {
    const subject = updated.rfq_subject ?? "Request for quotation";
    const body = updated.rfq_body ?? "Текст RFQ временно недоступен.";
    setSavedSubject(subject);
    setSavedBody(body);
    setDraftSubject(subject);
    setDraftBody(body);
    setCustomized(updated.rfq_is_customized);
    setReviewed(false);
    setTranslation(null);
    setTranslationVisible(false);
  };

  const toggleTranslation = async () => {
    if (translationVisible) {
      setTranslationVisible(false);
      return;
    }
    if (translation) {
      setTranslationVisible(true);
      return;
    }
    setTranslationBusy(true);
    setError(null);
    try {
      const result = await api.translateRfqPreview(rfq.id);
      setTranslation(result.translation_ru);
      setTranslationVisible(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setTranslationBusy(false);
    }
  };

  const saveDraft = async () => {
    const subject = draftSubject.trim();
    const body = draftBody.trim();
    if (!subject || !body) {
      setError("Заполните тему и текст RFQ.");
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const updated = await api.updateRfqMessageDraft(rfq.id, { subject, body });
      applyDraft(updated);
      setEditing(false);
      setNotice("Ручная версия RFQ сохранена.");
      onChanged();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  const resetDraft = async () => {
    if (
      !window.confirm(
        "Удалить ручные изменения и заново собрать RFQ по единому шаблону?",
      )
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const updated = await api.updateRfqMessageDraft(rfq.id, {
        subject: null,
        body: null,
      });
      applyDraft(updated);
      setEditing(false);
      setNotice("RFQ возвращён к единому шаблону.");
      onChanged();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  const dispatch = async () => {
    if (!reviewed || queued.length === 0) return;
    const names = queued
      .map((item) => item.supplier_company ?? `Поставщик #${item.supplier_id}`)
      .join(", ");
    if (
      !window.confirm(
        `Отправить показанный RFQ ${queued.length} получателям: ${names}? При включённых каналах это реальное внешнее действие.`,
      )
    ) {
      return;
    }

    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await api.dispatchRfq(rfq.id, true);
      const failed = result.filter((item) => item.status === "error");
      setRecipients(result);
      setReviewed(false);
      if (failed.length > 0) {
        setError(
          `Не отправлено: ${failed.length}. Проверьте контакты и настройки каналов.`,
        );
      } else {
        setNotice("RFQ отправлен выбранным поставщикам.");
      }
      onChanged();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
      await load();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rfq-dispatch-preparation">
      <section className="panel">
        <div className="tab-toolbar">
          <div>
            <h2>Поставщики</h2>
            <p className="note">
              Получатели, выбранные на предыдущем этапе. В отправку попадут
              только строки со статусом «ожидает отправки».
            </p>
          </div>
          {!readOnly && (
            <button className="secondary" onClick={onGoToSuppliers} type="button">
              Изменить выбор
            </button>
          )}
        </div>

        {recipients.length === 0 ? (
          <div className="rfq-recipient-empty">
            <p className="note">Поставщики для отправки ещё не выбраны.</p>
            {!readOnly && (
              <button onClick={onGoToSuppliers} type="button">
                Выбрать поставщиков
              </button>
            )}
          </div>
        ) : (
          <div className="rfq-recipient-list">
            {recipients.map((recipient) => (
              <div className="rfq-recipient-row" key={recipient.id}>
                <div>
                  <strong>
                    {recipient.supplier_company ?? `Поставщик #${recipient.supplier_id}`}
                  </strong>
                  <span className="note">
                    {recipient.channel === "email" ? "Email" : "WhatsApp"}
                  </span>
                </div>
                <div className="rfq-recipient-status">
                  <span
                    className={`badge ${
                      recipient.status === "sent"
                        ? "tone-ok"
                        : recipient.status === "error"
                          ? "tone-warn"
                          : "tone-neutral"
                    }`}
                  >
                    {STATUS_LABELS[recipient.status] ?? recipient.status}
                  </span>
                  {recipient.note && <span className="note">{recipient.note}</span>}
                  {!readOnly && recipient.status === "queued" && (
                    <button
                      className="secondary btn-small"
                      disabled={busy}
                      onClick={() => void removeRecipient(recipient)}
                      type="button"
                    >
                      Убрать
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className={`panel rfq-preview-panel${previewOpen ? " is-open" : ""}`}>
        <div className="tab-toolbar">
          <div>
            <h2>Предпросмотр RFQ</h2>
            <p className="note">
              Это точный английский текст первого сообщения, который получат
              выбранные поставщики.
            </p>
          </div>
          <div className="rfq-preview-actions">
            {customized && (
              <span className="badge tone-warn">изменено вручную</span>
            )}
            <span className="badge tone-neutral">получателей: {recipients.length}</span>
            <button
              aria-expanded={previewOpen}
              className="secondary btn-small"
              onClick={() => setPreviewOpen((open) => !open)}
              type="button"
            >
              {previewOpen ? "Свернуть" : "Развернуть"}
            </button>
            {!readOnly && previewOpen && !editing && (
              <button
                className="secondary btn-small"
                disabled={busy}
                onClick={beginEditing}
                type="button"
              >
                Редактировать
              </button>
            )}
          </div>
        </div>

        {previewOpen && (editing ? (
          <div className="rfq-preview-editor">
            <label>
              <span>Тема Email</span>
              <div className="rfq-subject-editor">
                <span>[RFQ-{rfq.id}]</span>
                <Input
                  maxLength={500}
                  value={draftSubject}
                  onChange={(event) => {
                    setDraftSubject(event.target.value);
                    setReviewed(false);
                  }}
                />
              </div>
            </label>
            <label>
              <span>Сообщение</span>
              <Textarea
                maxLength={20_000}
                rows={18}
                value={draftBody}
                onChange={(event) => {
                  setDraftBody(event.target.value);
                  setReviewed(false);
                }}
              />
            </label>
            <div className="rfq-editor-actions">
              <button
                className="secondary"
                disabled={busy}
                onClick={cancelEditing}
                type="button"
              >
                Отмена
              </button>
              <button
                disabled={busy || !draftSubject.trim() || !draftBody.trim()}
                onClick={() => void saveDraft()}
                type="button"
              >
                {busy ? "Сохранение…" : "Сохранить RFQ"}
              </button>
            </div>
          </div>
        ) : (
          <div className="rfq-preview-expanded">
            <div className="rfq-preview-message">
              <div className="rfq-preview-subject">
                <span>Тема Email</span>
                <strong>
                  [RFQ-{rfq.id}] {savedSubject}
                </strong>
              </div>
              <div className="rfq-preview-body">
                <span>Сообщение</span>
                <div>{savedBody}</div>
              </div>
            </div>
            <div className="rfq-preview-translation-actions">
              <button
                className="secondary btn-small"
                disabled={translationBusy || !savedBody.trim()}
                onClick={() => void toggleTranslation()}
                type="button"
              >
                {translationBusy
                  ? "Переводим…"
                  : translationVisible
                    ? "Скрыть перевод"
                    : "Перевести на русский"}
              </button>
            </div>
            {translationVisible && translation && (
              <div className="communication-message-translation rfq-preview-translation">
                <span>Русский перевод RFQ</span>
                <div>{translation}</div>
              </div>
            )}
          </div>
        ))}

        {previewOpen && !readOnly && customized && !editing && (
          <button
            className="rfq-reset-template"
            disabled={busy}
            onClick={() => void resetDraft()}
            type="button"
          >
            Вернуть единый шаблон
          </button>
        )}

        {notice && <p className="success-note">{notice}</p>}
        {error && <p className="error">{error}</p>}

        {previewOpen && !readOnly && !editing && queued.length > 0 && (
          <div className="rfq-preview-confirmation">
            <label>
              <input
                checked={reviewed}
                onChange={(event) => setReviewed(event.target.checked)}
                type="checkbox"
              />
              Я проверил RFQ, каналы и список получателей
            </label>
            <button
              disabled={busy || !reviewed}
              onClick={() => void dispatch()}
              type="button"
            >
              {busy ? "Отправка…" : `Отправить RFQ (${queued.length})`}
            </button>
          </div>
        )}
      </section>
    </div>
  );
}
