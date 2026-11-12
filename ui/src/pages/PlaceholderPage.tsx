import { useTranslation } from "react-i18next";

export function PlaceholderPage({ page }: { page: "studio" | "assets" | "elements" | "canvas" }) {
  const { t } = useTranslation();
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-3 px-6 py-16">
      <h1 className="font-display text-4xl font-bold uppercase">{t(`nav.${page}`)}</h1>
      <p className="text-muted">{t(`placeholder.${page}`)}</p>
    </div>
  );
}
