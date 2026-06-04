import { useState } from "react";
import { api, ApiError, type RFQCreatePayload } from "../api/client";
import type { RFQPreview, RFQRead, SubstanceInfo } from "../api/types";

const ALL_INCOTERMS = ["CIP", "FCA", "EXW"];

interface Props {
  onCreated: (rfq: RFQRead) => void;
}

export default function NewRfq({ onCreated }: Props) {
  const [cas, setCas] = useState("50-78-2");
  const [name, setName] = useState("Acetylsalicylic acid");
  const [purity, setPurity] = useState("USP");
  const [application, setApplication] = useState("");
  const [volume, setVolume] = useState("500 kg");
  const [incoterms, setIncoterms] = useState<string[]>(["CIP", "FCA", "EXW"]);

  const [substance, setSubstance] = useState<SubstanceInfo | null>(null);
  const [preview, setPreview] = useState<RFQPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const payload = (): RFQCreatePayload => ({
    cas,
    name,
    incoterms,
    purity: purity || null,
    application: application || null,
    volume: volume || null,
    channels: ["email"],
  });

  const toggleIncoterm = (code: string) =>
    setIncoterms((prev) =>
      prev.includes(code) ? prev.filter((c) => c !== code) : [...prev, code],
    );

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const onVerify = () =>
    run(async () => setSubstance(await api.verifyCas(cas)));

  const onPreview = () =>
    run(async () => setPreview(await api.previewRfq(payload())));

  const onCreate = () =>
    run(async () => {
      const rfq = await api.createRfq(payload(), true);
      onCreated(rfq);
    });

  return (
    <div className="panel">
      <h2>Новый запрос (RFQ)</h2>

      <div className="row">
        <div className="field">
          <label>CAS-номер</label>
          <input value={cas} onChange={(e) => setCas(e.target.value)} />
        </div>
        <div className="field">
          <label>Наименование</label>
          <input value={name} onChange={(e) => setName(e.target.value)} />
        </div>
      </div>

      <div className="row">
        <div className="field">
          <label>Чистота / грейд</label>
          <input value={purity} onChange={(e) => setPurity(e.target.value)} />
        </div>
        <div className="field">
          <label>Объём</label>
          <input value={volume} onChange={(e) => setVolume(e.target.value)} />
        </div>
      </div>

      <div className="field">
        <label>Применение</label>
        <textarea
          value={application}
          onChange={(e) => setApplication(e.target.value)}
        />
      </div>

      <div className="field">
        <label>Базисы поставки</label>
        <div className="checks">
          {ALL_INCOTERMS.map((code) => (
            <label key={code}>
              <input
                type="checkbox"
                checked={incoterms.includes(code)}
                onChange={() => toggleIncoterm(code)}
              />
              {code}
            </label>
          ))}
        </div>
      </div>

      <div className="actions">
        <button className="secondary" onClick={onVerify} disabled={busy || !cas}>
          Проверить CAS
        </button>
        <button className="secondary" onClick={onPreview} disabled={busy}>
          Предпросмотр письма
        </button>
        <button onClick={onCreate} disabled={busy || incoterms.length === 0}>
          Создать RFQ
        </button>
      </div>

      {error && <p className="error">Ошибка: {error}</p>}

      {substance && (
        <div className="field" style={{ marginTop: 12 }}>
          {substance.found ? (
            <span className="badge ok">
              CAS подтверждён · {substance.molecular_formula ?? ""}{" "}
              {substance.molecular_weight ? `· M=${substance.molecular_weight}` : ""}
            </span>
          ) : (
            <span className="badge err">
              Не подтверждён ({substance.error ?? "не найдено"})
            </span>
          )}
        </div>
      )}

      {preview && (
        <div style={{ marginTop: 12 }}>
          <div className="note">Тема: {preview.subject}</div>
          <pre className="letter">{preview.body}</pre>
        </div>
      )}
    </div>
  );
}
