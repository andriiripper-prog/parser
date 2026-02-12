#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ad Image Classifier - Классификация рекламных изображений по вертикалям
Использует EasyOCR для извлечения текста и Transformers для классификации
"""

import sys
import warnings
from pathlib import Path
from typing import Optional, Tuple, Dict

import torch
import easyocr
from transformers import pipeline

# Подавление предупреждений для чистого вывода
warnings.filterwarnings('ignore')


class AdImageClassifier:
    """
    Класс для классификации рекламных изображений по вертикалям.
    
    Использует:
    - EasyOCR для извлечения текста из изображений
    - HuggingFace Transformers для zero-shot классификации
    """
    
    # Определяем вертикали (категории) для классификации
    VERTICALS = [
        "Crypto & Investment",
        "Gambling & Casino",
        "Nutra & Health",
        "Dating",
        "News & Politics",
        "E-commerce",
        "AI & Technology",
        "Celebrities & Influencers"
    ]
    
    def __init__(
        self,
        ocr_languages: list = None,
        classification_model: str = "valhalla/distilbart-mnli-12-1"
    ):
        """
        Инициализация классификатора.
        
        Args:
            ocr_languages: Список языков для OCR (по умолчанию ['en', 'ru'])
            classification_model: Модель для классификации текста
        """
        if ocr_languages is None:
            ocr_languages = ['en', 'ru']
        
        self.ocr_languages = ocr_languages
        self.classification_model = classification_model
        self.device = self._get_device()
        
        print("🚀 Загрузка моделей...")
        print(f"   Используемое устройство: {self.device}")
        
        # Инициализация EasyOCR
        print("   📖 Загрузка EasyOCR...")
        self.ocr_reader = easyocr.Reader(
            self.ocr_languages,
            gpu=self.device == 'cuda'
        )
        
        # Инициализация классификатора
        print(f"   🤖 Загрузка модели классификации ({self.classification_model})...")
        self.classifier = pipeline(
            "zero-shot-classification",
            model=self.classification_model,
            device=0 if self.device == 'cuda' else -1
        )
        
        print("✅ Модели успешно загружены!\n")
    
    @staticmethod
    def _get_device() -> str:
        """
        Определяет доступное устройство (CUDA/CPU).
        
        Returns:
            str: 'cuda' если доступен GPU, иначе 'cpu'
        """
        if torch.cuda.is_available():
            return 'cuda'
        return 'cpu'
    
    def extract_text_from_image(self, image_path: str) -> Optional[str]:
        """
        Извлекает текст из изображения с помощью EasyOCR.
        
        Args:
            image_path: Путь к изображению
            
        Returns:
            str: Извлеченный текст или None, если текст не найден
            
        Raises:
            FileNotFoundError: Если файл изображения не найден
        """
        # Проверка существования файла
        if not Path(image_path).exists():
            raise FileNotFoundError(f"❌ Файл не найден: {image_path}")
        
        print(f"📷 Извлечение текста из изображения: {image_path}")
        
        try:
            # Извлечение текста
            results = self.ocr_reader.readtext(image_path)
            
            if not results:
                print("   ⚠️  Текст в изображении не обнаружен")
                return None
            
            # Объединение всех найденных текстовых фрагментов
            extracted_text = ' '.join([text for _, text, _ in results])
            
            print(f"   ✓ Извлечено {len(results)} текстовых фрагментов")
            return extracted_text.strip()
            
        except Exception as e:
            print(f"   ❌ Ошибка при извлечении текста: {e}")
            return None
    
    def classify_text(self, text: str) -> Dict[str, any]:
        """
        Классифицирует текст по вертикалям с помощью zero-shot classification.
        
        Args:
            text: Текст для классификации
            
        Returns:
            dict: Словарь с результатами классификации:
                  - vertical: название вертикали
                  - confidence: уверенность (0-1)
                  - all_scores: все оценки для каждой вертикали
        """
        if not text or len(text.strip()) == 0:
            return {
                'vertical': 'Unknown',
                'confidence': 0.0,
                'all_scores': {}
            }
        
        text = text.strip()
        print(f"🔍 Классификация текста (длина: {len(text)} символов)...")
        
        try:
            # Выполнение классификации
            result = self.classifier(
                text,
                candidate_labels=self.VERTICALS,
                multi_label=False
            )
            
            # Формирование результата
            top_label = result['labels'][0]
            top_score = result['scores'][0]
            
            # Создаем словарь всех оценок
            all_scores = dict(zip(result['labels'], result['scores']))
            
            return {
                'vertical': top_label,
                'confidence': top_score,
                'all_scores': all_scores
            }
            
        except Exception as e:
            print(f"   ❌ Ошибка при классификации: {e}")
            return {
                'vertical': 'Error',
                'confidence': 0.0,
                'all_scores': {},
                'error': str(e)
            }
    
    def classify_image(self, image_path: str) -> Dict[str, any]:
        """
        Полный цикл: извлечение текста и классификация изображения.
        
        Args:
            image_path: Путь к изображению
            
        Returns:
            dict: Результаты классификации с извлеченным текстом
        """
        # Извлечение текста
        extracted_text = self.extract_text_from_image(image_path)
        
        if not extracted_text:
            return {
                'image_path': image_path,
                'extracted_text': None,
                'vertical': 'No Text',
                'confidence': 0.0,
                'all_scores': {}
            }
        
        # Классификация
        classification_result = self.classify_text(extracted_text)
        
        # Объединение результатов
        return {
            'image_path': image_path,
            'extracted_text': extracted_text,
            **classification_result
        }
    
    def print_results(self, results: Dict[str, any]) -> None:
        """
        Красивый вывод результатов классификации.
        
        Args:
            results: Словарь с результатами классификации
        """
        print("\n" + "="*70)
        print("📊 РЕЗУЛЬТАТЫ КЛАССИФИКАЦИИ")
        print("="*70)
        print(f"Изображение: {results['image_path']}")
        print(f"\n📝 Извлеченный текст:")
        
        if results['extracted_text']:
            # Обрезаем длинный текст для удобства отображения
            text = results['extracted_text']
            if len(text) > 200:
                print(f"   {text[:200]}...")
            else:
                print(f"   {text}")
        else:
            print("   (текст не обнаружен)")
        
        print(f"\n🎯 Определенная вертикаль: {results['vertical']}")
        print(f"📈 Уверенность: {results['confidence']:.2%}")
        
        if results.get('all_scores'):
            print(f"\n� Все оценки:")
            for label, score in sorted(
                results['all_scores'].items(),
                key=lambda x: x[1],
                reverse=True
            ):
                bar_length = int(score * 30)
                bar = '█' * bar_length + '░' * (30 - bar_length)
                print(f"   {label:25s} {bar} {score:.2%}")
        
        print("="*70 + "\n")


def main():
    """
    Основная функция для тестирования классификатора.
    """
    # Путь к тестовому изображению
    test_image = 'test_ad.jpg'
    
    # Можно передать путь к изображению как аргумент командной строки
    if len(sys.argv) > 1:
        test_image = sys.argv[1]
    
    try:
        # Инициализация классификатора
        # Можно использовать более точную модель: "facebook/bart-large-mnli"
        # но она медленнее. По умолчанию используем легковесную версию
        classifier = AdImageClassifier(
            ocr_languages=['en', 'ru'],
            classification_model="valhalla/distilbart-mnli-12-1"
        )
        
        # Классификация изображения
        results = classifier.classify_image(test_image)
        
        # Вывод результатов
        classifier.print_results(results)
        
        # Возвращаем код завершения в зависимости от успеха
        return 0 if results['confidence'] > 0 else 1
        
    except FileNotFoundError as e:
        print(f"\n{e}")
        print("\n💡 Подсказка: Укажите путь к изображению как аргумент:")
        print(f"   python {Path(__file__).name} путь/к/изображению.jpg\n")
        return 1
        
    except Exception as e:
        print(f"\n❌ Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
