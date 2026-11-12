import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import en from "./en.json";

export const LANGUAGES = [{ code: "en", name: "English" }] as const;

void i18n.use(initReactI18next).init({
  resources: { en: { translation: en } },
  lng: "en",
  fallbackLng: "en",
  interpolation: { escapeValue: false },
  returnNull: false,
});

export default i18n;
