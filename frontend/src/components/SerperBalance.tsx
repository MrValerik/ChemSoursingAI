import { useEffect, useState } from "react";
import { getSerperBalance } from "../api/client";
import type { SerperBalance as Balance } from "../api/types";

export default function SerperBalance() {
  const [balance, setBalance] = useState<Balance | null>(null);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      try {
        const result = await getSerperBalance();
        if (active) setBalance(result);
      } catch {
        if (active) setBalance({ status: "unavailable", remaining_credits: null, checked_at: null });
      } finally {
        if (active) timer = setTimeout(refresh, 60_000);
      }
    };
    void refresh();
    return () => { active = false; clearTimeout(timer); };
  }, []);
  const label = !balance ? "получение лимита…"
    : balance.status === "ok" ? `остаток: ${balance.remaining_credits!.toLocaleString("ru-RU")} кредитов`
    : balance.status === "not_configured" ? "ключ не настроен" : "лимит недоступен";
  const title = balance?.checked_at
    ? `Баланс Serper на ${new Date(balance.checked_at).toLocaleString("ru-RU")}. Обновляется раз в минуту.`
    : "Остаток кредитов Serper. Обновляется раз в минуту.";
  return <span className="serper-balance" role="status" title={title}>Serper · {label}</span>;
}
