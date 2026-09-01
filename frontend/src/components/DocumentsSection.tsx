import { useCallback, useEffect, useState } from "react";
import { api, ApiError, userErrorMessage } from "../api/client";
import type { SupplierDocumentRead } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { Icon, Toast } from "./ui";

const KIND_LABELS: Record<string, string> = {
  coa: "Паспорт качества (CoA)",
  tds: "Спецификация (TDS)",
  msds: "Паспорт безопасности",
  other: "Другой документ",
};

const TEXT_STATUS_LABELS: Record<string, string> = {
  stored: "Текст ещё не извлечён",
  extracted: "Текст извлечён",
  ocr_extracted: "Текст распознан (OCR)",
  needs_ocr: "Скан без текстового слоя",
  unsupported: "Формат не поддерживается",
  failed: "Не удалось прочитать",
};

const VERIFICATION_LABELS: Record<string, string> = {
  confirmed: "Документ подтверждён",
  needs_review: "Нужна ручная проверка",
  rejected: "Документ отклонён",
  unavailable: "Проверка не выполнена",
};

const verificationTone = (status: string | undefined) => {
  if (status === "confirmed") return "tone-ok";
  if (status === "rejected") return "tone-danger";
  if (status === "needs_review") return "tone-warn";
  return "tone-neutral";
};

const formatSize = (bytes: number) =>
  bytes >= 1024 * 1024
    ? `${(bytes / 1024 / 1024).toFixed(1)} МБ`
    : `${Math.max(1, Math.round(bytes / 1024))} КБ`;

// Исходы сверки изготовителя человеческим языком. Цвет идёт вместе с
// текстом: смысл несёт текст, цвет только помогает его найти.
const MANUFACTURER_MATCH_LABELS: Record<string, string> = {
  match: "Изготовитель подтверждён паспортом",
  mismatch: "Паспорт выпущен другой компанией",
  manual_review: "Изготовителя нужно сверить вручную",
  insufficient: "Изготовителя в документе нет",
};

export default function DocumentsSection({ rfqId }: { rfqId: number }) {
  const { user } = useAuth();
  const readOnly = user?.role === "auditor";
  const [documents, setDocuments] = useState<SupplierDocumentRead[]>([]);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [downloadBusyId, setDownloadBusyId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setDocuments(await api.listRfqDocuments(rfqId));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : String(caught));
    }
  }, [rfqId]);

  useEffect(() => {
    void load();
  }, [load]);

  const verify = async (documentId: number) => {
    setBusyId(documentId);
    setError(null);
    setNotice(null);
    try {
      const updated = await api.verifyDocument(documentId);
      setDocuments((current) =>
        current.map((item) => (item.id === documentId ? updated : item)),
      );
      setNotice(
        `Проверка завершена: ${
          VERIFICATION_LABELS[updated.verification?.status ?? ""] ??
          "результат сохранён"
        }.`,
      );
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? userErrorMessage(caught.message)
          : String(caught),
      );
    } finally {
      setBusyId(null);
    }
  };

  const download = async (document: SupplierDocumentRead) => {
    setDownloadBusyId(document.id);
    setError(null);
    try {
      const blob = await api.downloadDocument(document.id);
      const url = URL.createObjectURL(blob);
      const link = window.document.createElement("a");
      link.href = url;
      link.download = document.filename;
      window.document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? userErrorMessage(caught.message)
          : String(caught),
      );
    } finally {
      setDownloadBusyId(null);
    }
  };

  if (documents.length === 0) {
    return (
      <div className="panel">
        <h2>Документы поставщиков</h2>
        {error && <p className="error">{error}</p>}
        <p className="note">
          Пока нет ни одного документа. Файлы появляются здесь автоматически,
          когда поставщик присылает паспорт качества или спецификацию в ответ
          на письмо.
        </p>
      </div>
    );
  }

  return (
    <div className="panel">
      <h2>Документы поставщиков</h2>
      <p className="note">
        Проверка сверяет документ с веществом запроса и подтверждает выводы
        только дословными цитатами из самого файла. Она не заменяет решение
        специалиста о пригодности партии.
      </p>
      {error && <p className="error">{error}</p>}
      {notice && <Toast message={notice} onClose={() => setNotice(null)} />}

      <div className="document-list">
        {documents.map((document) => {
          const verification = document.verification;
          const status = verification?.status;
          return (
            <article className="document-card" key={document.id}>
              <div className="document-card-head">
                <div>
                  <strong>{document.filename}</strong>
                  <small>
                    {KIND_LABELS[document.kind] ?? document.kind} ·{" "}
                    {formatSize(document.size_bytes)}
                    {document.page_count ? ` · ${document.page_count} стр.` : ""}
                  </small>
                </div>
                <div className="document-card-actions">
                  {status && (
                    <span className={`badge ${verificationTone(status)}`}>
                      {VERIFICATION_LABELS[status] ?? status}
                    </span>
                  )}
                  <button
                    className="secondary"
                    disabled={downloadBusyId === document.id}
                    onClick={() => void download(document)}
                    type="button"
                  >
                    <Icon name="save" size={14} />
                    {downloadBusyId === document.id ? "Скачивание…" : "Скачать"}
                  </button>
                  {!readOnly &&
                    (document.text_status === "extracted" ||
                      document.text_status === "ocr_extracted") && (
                    <button
                      className="secondary"
                      disabled={busyId === document.id}
                      onClick={() => void verify(document.id)}
                      type="button"
                    >
                      {busyId === document.id
                        ? "Проверка…"
                        : status
                          ? "Проверить заново"
                          : "Проверить документ"}
                    </button>
                  )}
                </div>
              </div>

              {document.text_status !== "extracted" &&
                document.text_status !== "ocr_extracted" && (
                <p className="qualification-warning">
                  {TEXT_STATUS_LABELS[document.text_status] ??
                    document.text_status}
                  {document.extraction_error
                    ? `: ${document.extraction_error}`
                    : ""}
                  {document.text_status === "needs_ocr"
                    ? ". Проверить такой документ можно только вручную, пока не подключено распознавание сканов."
                    : ""}
                </p>
              )}

              {verification && (
                <div className="document-verification">
                  <p>{verification.gate_reason}</p>
                  <p>
                    <strong>
                      {verification.confidence_breakdown?.length
                        ? `Проверяемая уверенность: ${verification.confidence}%`
                        : "Требуется повторная проверка по новой формуле"}
                    </strong>
                  </p>
                  {verification.reason && (
                    <p className="note">{verification.reason}</p>
                  )}
                  <dl className="document-facts">
                    <dt>CAS в запросе</dt>
                    <dd>{verification.expected_cas || "—"}</dd>
                    <dt>CAS в документе</dt>
                    <dd>
                      {verification.cas_in_document?.length
                        ? verification.cas_in_document.join(", ")
                        : "не найден"}
                    </dd>
                  </dl>

                  {/* Изготовитель из паспорта против компании, приславшей
                      документ. Обе строки показываются как есть: решение
                      принимает человек, и сравнивать ему нужно то, что
                      написано, а не то, что осталось после нормализации. */}
                  {verification.manufacturer_match &&
                    verification.manufacturer_match.status !== "insufficient" && (
                      <div
                        className={`document-manufacturer is-${verification.manufacturer_match.status}`}
                      >
                        <strong>
                          {MANUFACTURER_MATCH_LABELS[
                            verification.manufacturer_match.status
                          ]}
                        </strong>
                        <dl className="document-facts">
                          <dt>Изготовитель в паспорте</dt>
                          <dd>
                            {verification.manufacturer_match.document_manufacturer ||
                              "—"}
                          </dd>
                          <dt>Прислал документ</dt>
                          <dd>
                            {verification.manufacturer_match.supplier_company || "—"}
                          </dd>
                        </dl>
                        <p className="note">
                          {verification.manufacturer_match.reason}
                        </p>
                        {verification.manufacturer_match.quote && (
                          <p className="document-quote">
                            «{verification.manufacturer_match.quote}»
                          </p>
                        )}
                        {verification.manufacturer_match.lead && (
                          <p className="note">
                            Наводка для нового поиска:{" "}
                            <strong>
                              {verification.manufacturer_match.lead}
                            </strong>
                            . Подтверждённым поставщиком эта компания не
                            становится — о ней известно только имя из чужого
                            документа.
                          </p>
                        )}
                      </div>
                    )}

                  {(verification.confidence_breakdown?.length ?? 0) > 0 && (
                    <details className="trace-subdetails">
                      <summary>Из чего складывается уверенность</summary>
                      <ul className="document-claims">
                        {verification.confidence_breakdown?.map((factor) => (
                          <li key={`confidence-${document.id}-${factor.key}`}>
                            <strong>
                              {factor.label}: {factor.score >= 0 ? "+" : ""}
                              {factor.score}
                              {factor.max_score > 0 ? `/${factor.max_score}` : ""}
                            </strong>
                            <small>{factor.reason}</small>
                          </li>
                        ))}
                      </ul>
                      {verification.model_confidence != null && (
                        <p className="note">
                          Справочная уверенность классификатора: {verification.model_confidence}%.
                          Она не определяет итоговый процент.
                        </p>
                      )}
                    </details>
                  )}

                  {verification.accepted_claims?.length > 0 && (
                    <details className="trace-subdetails" open>
                      <summary>
                        Подтверждено цитатами (
                        {verification.accepted_claims.length})
                      </summary>
                      <ul className="document-claims">
                        {verification.accepted_claims.map((claim, index) => (
                          <li key={`ok-${document.id}-${index}`}>
                            <strong>{claim.claim_value}</strong>
                            <blockquote>«{claim.quote}»</blockquote>
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}

                  {verification.rejected_claims?.length > 0 && (
                    <details className="trace-subdetails">
                      <summary>
                        Отклонено проверкой цитат (
                        {verification.rejected_claims.length})
                      </summary>
                      <ul className="document-claims rejected">
                        {verification.rejected_claims.map((claim, index) => (
                          <li key={`bad-${document.id}-${index}`}>
                            <strong>{claim.claim_value}</strong>
                            <small>{claim.rejection_reason}</small>
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}

                  {verification.red_flags?.length > 0 && (
                    <ul className="document-flags">
                      {verification.red_flags.map((flag, index) => (
                        <li key={`flag-${document.id}-${index}`}>{flag}</li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </article>
          );
        })}
      </div>
    </div>
  );
}
