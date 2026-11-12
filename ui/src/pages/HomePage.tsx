import { Link } from "@tanstack/react-router";
import { Clapperboard, Image } from "lucide-react";
import { useTranslation } from "react-i18next";
import { $api } from "@/api/client";
import { buttonVariants } from "@/components/ui/button";
import { BackendStatus } from "@/features/status/BackendStatus";

export function HomePage() {
  const { t } = useTranslation();
  const { data: info } = $api.useQuery("get", "/api/system/info");
  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-10 px-6 py-12">
      <section className="flex flex-col gap-4">
        <span className="font-mono text-xs uppercase tracking-[0.2em] text-tungsten">{t("home.eyebrow")}</span>
        <h1 className="max-w-3xl font-display text-5xl font-bold uppercase leading-[0.95] tracking-tight sm:text-6xl">
          {t("home.title")}
        </h1>
        <p className="max-w-2xl text-base text-muted">{t("home.lede")}</p>
        <div className="flex flex-wrap gap-2 pt-2">
          <Link to="/image" className={buttonVariants({ variant: "primary", size: "lg" })}>
            <Image />
            {t("home.createImage")}
          </Link>
          <Link to="/video" className={buttonVariants({ variant: "outline", size: "lg" })}>
            <Clapperboard />
            {t("home.createVideo")}
          </Link>
        </div>
      </section>
      <section aria-labelledby="machine" className="flex flex-col gap-3 rounded-2xl border border-line bg-flat p-5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 id="machine" className="font-display text-xl font-semibold uppercase tracking-wide">
            {t("home.machine")}
          </h2>
          <BackendStatus />
        </div>
        {info ? (
          <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-2">
            <div>
              <dt className="font-mono text-[11px] uppercase tracking-wider text-muted">{t("home.dataDir")}</dt>
              <dd className="break-all font-mono text-xs">{info.data_dir}</dd>
            </div>
            <div>
              <dt className="font-mono text-[11px] uppercase tracking-wider text-muted">{t("home.schema")}</dt>
              <dd className="font-mono text-xs">{info.schema_revision}</dd>
            </div>
            <div>
              <dt className="font-mono text-[11px] uppercase tracking-wider text-muted">OS · Python</dt>
              <dd className="font-mono text-xs">
                {info.platform} · {info.python}
              </dd>
            </div>
            <div>
              <dt className="font-mono text-[11px] uppercase tracking-wider text-muted">{t("home.ffmpeg")}</dt>
              <dd className="font-mono text-xs">{info.tools.ffmpeg ? "found" : "missing"}</dd>
            </div>
          </dl>
        ) : null}
      </section>
    </div>
  );
}
