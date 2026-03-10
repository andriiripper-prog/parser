#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ad Image Classifier
Двойная классификация рекламных креативов:
  1. EasyOCR  — вытаскивает весь текст с картинки
  2. CLIP     — визуально классифицирует изображение по вертикалям
Итог: взвешенное среднее CLIP (60%) + zero-shot на OCR тексте (40%)
"""

import os
import sys
import warnings
import contextlib
from pathlib import Path
from typing import Optional, Dict

import torch
from PIL import Image
from transformers import (
    CLIPProcessor,
    CLIPModel,
    pipeline,
)

warnings.filterwarnings("ignore")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


# ---------------------------------------------------------------------------
# Фикс EPIPE: подавляем stdout/stderr на уровне FD при инициализации EasyOCR
# ---------------------------------------------------------------------------
@contextlib.contextmanager
def _suppress_fd():
    devnull = open(os.devnull, "w")
    old_out = os.dup(1)
    old_err = os.dup(2)
    try:
        os.dup2(devnull.fileno(), 1)
        os.dup2(devnull.fileno(), 2)
        yield
    finally:
        os.dup2(old_out, 1)
        os.dup2(old_err, 2)
        os.close(old_out)
        os.close(old_err)
        devnull.close()


class AdImageClassifier:
    """
    Классификатор рекламных объявлений по вертикалям.
    Адаптировано под арбитражный трафик: Fake News, Clickbait, Financial/Gov Scams.
    """

    VERTICALS = {
        "Fake News & Media Scandals": (
            "breaking news broadcast interrupted live tv scandal microphone left on "
            "anchor walked off set deleted interview censored broadcast media cover-up "
            "shocking announcement live on air television studio argument breaking story "
            "news channel logo exclusive report "
            "son dakika haberleri yayın kesildi canlı tv skandal açık kalan mikrofon setten ayrılan sunucu "
            "silinmiş röportaj sansürlü yayın medya örtbas şoke eden duyuru canlı yayında televizyon stüdyosu "
            "tartışması son dakika haberi haber kanalı logosu özel haber "
            "nouvelles de dernière minute émission interrompue scandale tv direct micro oublié "
            "journaliste parti interview supprimée censuré couverture médiatique annonce choc en direct "
            "studio télévision reportage exclusif chaîne d'information "
            "breaking nieuws uitzending onderbroken live tv schandaal microfoon vergeten "
            "presentator weggelopen verwijderd interview gecensureerd mediaberichtgeving "
            "schokkende aankondiging live televisie studio exclusief nieuwskanaal"
        ),
        "Celebrity Tragedy & Legal Drama": (
            "famous public figure businessman politician arrested police escort handcuffs "
            "hospital bed tragic accident career ending lawsuit sued for saying this "
            "shocking discovery leaked documents private emails exposed secret files "
            "reputation ruined VIP scandal "
            "ünlü halk figürü iş insanı politikacı tutuklandı polis eskortu kelepçe "
            "hastane yatağı trajik kaza kariyer bitiren dava bunu söylediği için dava edildi "
            "şok edici keşif sızdırılan belgeler özel e-postalar ifşa edilen gizli dosyalar "
            "mahvolmuş itibar VIP skandalı "
            "célébrité personnalité publique homme d'affaires politicien arrêté escorte policière menottes "
            "lit d'hôpital accident tragique procès carrière brisée documents divulgués fichiers secrets exposés "
            "réputation détruite scandale VIP "
            "beroemdheid publieke figuur zakenman politicus gearresteerd politie-escorte handboeien "
            "ziekenhuisbed tragisch ongeluk rechtszaak carrière verwoest gelekte documenten geheime bestanden "
            "reputatie geruïneerd VIP schandaal"
        ),
        "Government Payouts & Allowances": (
            "attention citizens age eligibility check government payment subsidy "
            "financial relief social program approved payout ATM cash withdrawal "
            "claim your money demographic targeting welfare benefit national flag "
            "official announcement tax return "
            "dikkat vatandaşlar yaş uygunluk kontrolü devlet ödemesi sübvansiyon "
            "mali yardım onaylanmış ödeme ATM nakit çekimi paranızı talep edin "
            "demografik hedefleme sosyal yardım ulusal bayrak resmi duyuru vergi iadesi "
            "attention citoyens vérification d'éligibilité paiement gouvernemental subvention "
            "aide financière programme social versement approuvé retrait ATM réclamez votre argent "
            "ciblage démographique aide sociale drapeau national annonce officielle remboursement d'impôt "
            "aandacht burgers leeftijdscontrole overheidsuitkering subsidie financiële ondersteuning "
            "sociaal programma goedgekeerde uitbetaling geldopname ATM claim uw geld "
            "demografisch targeting sociale uitkering nationale vlag officiële aankondiging belastingteruggave"
        ),
        "Financial Secrets & Wealth Exposé": (
            "bank CEO exposed central bank hiding truth secret wealth system "
            "picking pockets massive profits hidden inheritance last will and testament "
            "legal document notary financial loophole revealing a secret banking conspiracy "
            "economy crisis exposed truth "
            "banka CEO'su ifşa oldu merkez bankası gerçeği saklıyor gizli servet sistemi "
            "büyük karlar gizli miras vasiyetname yasal belge noter finansal boşluk "
            "gizli bankacılık komplosu ekonomik kriz ifşa edilen gerçek "
            "PDG de banque exposé banque centrale cachant la vérité système de richesse secret "
            "profits massifs héritage caché testament document légal notaire faille financière "
            "complot bancaire secret crise économique vérité révélée "
            "bank CEO ontmaskerd centrale bank verbergt waarheid geheim rijkdomsysteem "
            "enorme winsten verborgen erfenis testament juridisch document notaris financiële maagd "
            "geheim bankcomplot economische crisis waarheid onthuld"
        ),
        "Crypto & Investment Offers": (
            "cryptocurrency platform trading bot automated investment passive income "
            "multiply savings financial independence crypto scheme get rich quick "
            "guaranteed returns daily profit trading app "
            "kripto para platformu ticaret botu otomatik yatırım pasif gelir birikimleri katla "
            "finansal bağımsızlık kripto planı kolay yoldan zengin olma garantili getiriler "
            "günlük kar ticaret uygulaması "
            "plateforme cryptomonnaie bot de trading investissement automatisé revenu passif "
            "multiplier épargne indépendance financière schéma crypto s'enrichir rapidement "
            "rendements garantis profit quotidien application de trading "
            "cryptovaluta platform handelsbot geautomatiseerde investering passief inkomen "
            "spaargeld vermenigvuldigen financiële onafhankelijkheid crypto snel rijk worden "
            "gegarandeerd rendement dagelijkse winst trading app"
        ),
        # ───── БЕЛЫЙ СПИСОК — реклама которую мы ПРОПУСКАЕМ ─────────────────
        "IGNORE: B2B & SaaS Software": (
            "B2B enterprise software SaaS cloud platform data analytics API integration "
            "developer tools dashboard CRM ERP business intelligence data warehouse "
            "corporate IT infrastructure HR payroll remote work productivity suite "
            "digital transformation tech startup professional services workflow automation "
            "business phone system project management compliance solution "
            "B2B kurumsal yazılım SaaS bulut platformu veri analitiği API entegrasyonu "
            "geliştirici araçları kontrol paneli CRM ERP iş zekası veri ambarı kurumsal BT "
            "altyapısı İK bordro uzaktan çalışma üretkenlik paketi dijital dönüşüm teknoloji "
            "girişimi profesyonel hizmetler iş akışı otomasyonu iş telefonu sistemi proje yönetimi uyumluluk çözümü"
        ),
        "IGNORE: Legitimate News & Media": (
            "newspaper subscription journalism digital access magazine article "
            "independent media editorial global coverage data-driven ranking "
            "press credible source published report breaking investigation "
            "daily briefing newsletter podcast live event broadcast network "
            "reporter journalist correspondent newsroom editorial staff "
            "gazete aboneliği gazetecilik dijital erişim dergi makalesi bağımsız medya başyazı "
            "küresel kapsam veriye dayalı sıralama basın güvenilir kaynak yayınlanmış rapor "
            "son dakika soruşturması günlük brifing bülten podcast canlı etkinlik yayın ağı "
            "muhabir haber odası yayın kadrosu"
        ),
        "IGNORE: General E-commerce & Retail": (
            "online store retail shop product catalog sale discount coupon "
            "fashion clothing shoes accessories home decor furniture electronics "
            "grocery delivery food order restaurant menu customer review "
            "add to cart buy now checkout shipping free delivery returns "
            "brand logo product photo white background standard ad banner "
            "online mağaza perakende satış ürün kataloğu indirim kuponu moda giyim ayakkabı "
            "aksesuarlar ev dekorasyonu mobilya elektronik market teslimatı yemek siparişi "
            "restoran menüsü müşteri değerlendirmesi sepete ekle şimdi al ödeme kargo ücretsiz "
            "teslimat iadeler marka logosu ürün fotoğrafı beyaz arka plan standart reklam afişi"
        ),
        "IGNORE: Health & Beauty (Legitimate)": (
            "skincare cosmetics beauty routine moisturizer supplement vitamin "
            "fitness gym workout healthy lifestyle organic food diet nutrition "
            "pharmacy drugstore medical device hospital clinic doctor appointment "
            "dental care eye care personal hygiene wellness brand "
            "cilt bakımı kozmetik güzellik rutini nemlendirici takviye vitamin fitness spor salonu "
            "egzersiz sağlıklı yaşam tarzı organik gıda diyet beslenme eczane tıbbi cihaz "
            "hastane klinik doktor randevusu diş bakımı göz bakımı kişisel hijyen sağlık markası"
        ),
        "IGNORE: Education & Career": (
            "online course university degree certification skill training "
            "e-learning platform student enrollment scholarship tuition "
            "career opportunity job posting hiring recruitment resume "
            "professional development language learning coding bootcamp "
            "MBA program workshop webinar conference "
            "online kurs üniversite derecesi sertifikasyon beceri eğitimi e-öğrenme platformu "
            "öğrenci kaydı burs öğrenim ücreti kariyer fırsatı iş ilanı işe alım özgeçmiş "
            "mesleki gelişim dil öğrenimi kodlama eğitim kampı MBA programı atölye veya seminer konferans"
        ),
        "IGNORE: Charity & NGO": (
            "charity donation donate non-profit NGO humanitarian aid relief fund "
            "give monthly monthly donor fundraising hunger crisis food basket "
            "feed a family winter relief protect children orphan emergency response "
            "hospital support medical aid global crisis Ramadan impact Zakat Sadaqah "
            "Gaza Yemen Africa UNICEF Red Cross IDRF volunteer community support philanthropic "
            "hayır kurumu bağış yardım sivil toplum kuruluşu insani yardım fonu aylık bağışçı "
            "bağış toplama açlık krizi gıda paketi bir aileyi doyur kış yardımı çocukları koruyun "
            "yetim acil müdahale hastane desteği tıbbi yardım küresel kriz Ramazan etkisi Zekat Sadaka "
            "Gazze Yemen Afrika UNICEF Kızılhaç gönüllü topluluk desteği hayırsever"
        ),
        "Other": (
            "general advertisement regular promotion product e-commerce standard banner "
            "genel reklam düzenli promosyon ürün e-ticaret standart afiş "
            "publicité générale promotion régulière produit e-commerce bannière standard "
            "algemene advertentie reguliere promotie product e-commerce standaard banner"
        ),
    }

    # Веса при объединении CLIP + zero-shot
    CLIP_WEIGHT = 0.6
    ZS_WEIGHT = 0.4

    # Модели
    CLIP_MODEL_ID = "openai/clip-vit-base-patch32"
    ZS_MODEL_ID = "valhalla/distilbart-mnli-12-1"

    def __init__(
        self,
        ocr_languages: list = None,
        classification_model: str = None,
        clip_model: str = None,
    ):
        if ocr_languages is None:
            ocr_languages = ["en", "ru", "tr", "fr", "nl"]
        self.ocr_languages = ocr_languages
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.ocr_reader = None

        self._labels = list(self.VERTICALS.keys())
        self._descriptions = list(self.VERTICALS.values())

        clip_id = clip_model or self.CLIP_MODEL_ID
        zs_id = classification_model or self.ZS_MODEL_ID

        print("🚀 Загрузка моделей...")
        print(f"   Устройство: {self.device}")

        # ---- EasyOCR (с фиксом EPIPE) ----
        print("   📖 Загрузка EasyOCR...")
        try:
            import easyocr
            with _suppress_fd():
                self.ocr_reader = easyocr.Reader(
                    self.ocr_languages,
                    gpu=(self.device == "cuda"),
                    verbose=False,
                )
            print("   ✅ EasyOCR готов")
        except Exception as e:
            print(f"   ⚠️  EasyOCR недоступен: {e}")

        # ---- CLIP ----
        print(f"   🖼  Загрузка CLIP ({clip_id})...")
        self.clip_model = CLIPModel.from_pretrained(clip_id)
        self.clip_processor = CLIPProcessor.from_pretrained(clip_id)
        self.clip_model.eval()
        if self.device == "cuda":
            self.clip_model = self.clip_model.cuda()
        print("   ✅ CLIP готов")

        # ---- Zero-shot text classifier ----
        print(f"   🤖 Загрузка zero-shot ({zs_id})...")
        self.zs_classifier = pipeline(
            "zero-shot-classification",
            model=zs_id,
            device=0 if self.device == "cuda" else -1,
        )
        print("✅ Все модели загружены!\n")

    # -----------------------------------------------------------------------
    # EasyOCR
    # -----------------------------------------------------------------------

    def extract_text_from_image(self, image_path: str) -> Optional[str]:
        """Извлекает весь текст с креатива через EasyOCR."""
        if not self.ocr_reader or not Path(image_path).exists():
            return None
        try:
            results = self.ocr_reader.readtext(image_path, detail=0)
            return " ".join(results).strip() or None
        except Exception as e:
            print(f"   ⚠️  OCR error: {e}")
            return None

    # -----------------------------------------------------------------------
    # CLIP — визуальная классификация
    # -----------------------------------------------------------------------

    def _clip_classify(self, image_path: str) -> Dict[str, float]:
        """
        Прогоняет изображение через CLIP с описаниями вертикалей.
        Возвращает {label: score} — сумма вероятностей = 1.
        """
        try:
            image = Image.open(image_path).convert("RGB")
            # Используем описания вертикалей как текстовые подсказки CLIP
            inputs = self.clip_processor(
                text=self._descriptions,
                images=image,
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=77,  # лимит CLIP
            )
            if self.device == "cuda":
                inputs = {k: v.cuda() for k, v in inputs.items()}

            with torch.no_grad():
                outputs = self.clip_model(**inputs)
                logits = outputs.logits_per_image  # [1, n_labels]
                probs = logits.softmax(dim=-1).squeeze().tolist()

            if isinstance(probs, float):
                probs = [probs]

            return dict(zip(self._labels, probs))
        except Exception as e:
            print(f"   ⚠️  CLIP error: {e}")
            return {}

    # -----------------------------------------------------------------------
    # Zero-shot — классификация OCR текста
    # -----------------------------------------------------------------------

    def _zs_classify(self, text: str) -> Dict[str, float]:
        """Zero-shot классификация OCR текста по описаниям вертикалей."""
        if not text or not text.strip():
            return {}
        try:
            result = self.zs_classifier(
                text[:512],
                candidate_labels=self._descriptions,
                multi_label=False,
            )
            desc_to_label = dict(zip(self._descriptions, self._labels))
            return {
                desc_to_label.get(d, d): s
                for d, s in zip(result["labels"], result["scores"])
            }
        except Exception as e:
            print(f"   ⚠️  Zero-shot error: {e}")
            return {}

    # -----------------------------------------------------------------------
    # Публичный API
    # -----------------------------------------------------------------------

    def classify_image(self, image_path: str) -> Dict:
        """
        Главный метод: EasyOCR + CLIP → взвешенное среднее → вертикаль.

        Шаги:
          1. EasyOCR достаёт весь текст с картинки
          2. CLIP классифицирует саму картинку визуально
          3. Если есть OCR текст — zero-shot по нему (вес 40%)
          4. Итог = CLIP * 0.6 + zero-shot * 0.4

        Returns:
            dict: vertical, confidence, all_scores, extracted_text,
                  clip_scores, zs_scores
        """
        # 1. OCR
        ocr_text = self.extract_text_from_image(image_path) or ""
        print(f"   📝 OCR: {repr(ocr_text[:100]) if ocr_text else '(пусто)'}")

        # 2. CLIP по картинке
        clip_scores = self._clip_classify(image_path)

        # 3. Zero-shot по OCR тексту (если есть)
        zs_scores = self._zs_classify(ocr_text) if ocr_text else {}

        # 4. Объединяем
        if clip_scores and zs_scores:
            # Взвешенное среднее
            combined = {}
            for label in self._labels:
                c = clip_scores.get(label, 0.0)
                z = zs_scores.get(label, 0.0)
                combined[label] = self.CLIP_WEIGHT * c + self.ZS_WEIGHT * z
        elif clip_scores:
            combined = clip_scores   # только CLIP
        elif zs_scores:
            combined = zs_scores     # только текст
        else:
            return {
                "image_path": image_path,
                "extracted_text": ocr_text or None,
                "vertical": "No Text",
                "confidence": 0.0,
                "all_scores": {},
                "clip_scores": {},
                "zs_scores": {},
            }

        # 5. Топовая вертикаль
        top_label = max(combined, key=combined.get)
        top_score = combined[top_label]

        # 6. Белый список — если топ это IGNORE-категория, сразу отмечаем
        is_whitelist = top_label.startswith("IGNORE:")
        return {
            "image_path": image_path,
            "extracted_text": ocr_text or None,
            "vertical": top_label,
            "confidence": top_score,
            "is_whitelist": is_whitelist,
            "all_scores": combined,
            "clip_scores": clip_scores,
            "zs_scores": zs_scores,
        }

    def classify_text(self, text: str) -> Dict:
        """Классификация произвольного текста (zero-shot only)."""
        scores = self._zs_classify(text)
        if not scores:
            return {"vertical": "Unknown", "confidence": 0.0, "all_scores": {}}
        top = max(scores, key=scores.get)
        return {"vertical": top, "confidence": scores[top], "all_scores": scores}

    def print_results(self, results: Dict) -> None:
        print("\n" + "=" * 72)
        print("📊 РЕЗУЛЬТАТЫ КЛАССИФИКАЦИИ")
        print("=" * 72)
        print(f"Файл   : {results.get('image_path', '-')}")
        ocr = results.get("extracted_text") or "(текст не найден)"
        print(f"OCR    : {ocr[:200]}{'...' if len(ocr) > 200 else ''}")
        print(f"\n🎯 Вертикаль  : {results['vertical']}")
        print(f"📈 Уверенность: {results['confidence']:.2%}")

        if results.get("all_scores"):
            print("\n📊 Итоговые оценки (CLIP×0.6 + ZS×0.4):")
            for label, score in sorted(results["all_scores"].items(), key=lambda x: -x[1]):
                bar = "█" * int(score * 40) + "░" * (40 - int(score * 40))
                print(f"   {label:40s} {bar} {score:.2%}")

        if results.get("clip_scores"):
            print("\n🖼  CLIP scores:")
            for label, score in sorted(results["clip_scores"].items(), key=lambda x: -x[1]):
                print(f"   {label:40s} {score:.2%}")

        if results.get("zs_scores"):
            print("\n📝 Zero-shot (OCR text) scores:")
            for label, score in sorted(results["zs_scores"].items(), key=lambda x: -x[1]):
                print(f"   {label:40s} {score:.2%}")
        print("=" * 72 + "\n")


def main():
    test_image = sys.argv[1] if len(sys.argv) > 1 else "test_ad.jpg"
    c = AdImageClassifier(ocr_languages=["en", "ru"])
    results = c.classify_image(test_image)
    c.print_results(results)
    return 0 if results["confidence"] > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
