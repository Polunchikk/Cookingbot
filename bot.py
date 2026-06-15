import asyncio
import logging
import aiohttp
import sqlite3
from datetime import datetime
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, \
    InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from dotenv import load_dotenv
import os
from deep_translator import GoogleTranslator

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise ValueError("Токен бота не найден. Создайте файл .env с BOT_TOKEN=ваш_токен")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ==================== ПЕРЕВОДЧИК ====================
translator = GoogleTranslator(source='auto', target='ru')

# ==================== КЛАВИАТУРЫ ====================
main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🔍 Поиск рецепта")],
        [KeyboardButton(text="⭐ Мои избранные")],
        [KeyboardButton(text="🏆 Топ популярных")],
        [KeyboardButton(text="❓ Помощь")]
    ],
    resize_keyboard=True
)

cancel_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="❌ Отмена")]],
    resize_keyboard=True
)


class SearchState(StatesGroup):
    waiting_for_query = State()


# ==================== БАЗА ДАННЫХ ====================
DB_NAME = "recipes.db"


def init_db():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER UNIQUE,
        username TEXT,
        joined_at TEXT
    )''')
    cur.execute('''CREATE TABLE IF NOT EXISTS favorites (
        user_id INTEGER,
        recipe_id TEXT,
        recipe_title TEXT,
        added_at TEXT,
        PRIMARY KEY (user_id, recipe_id)
    )''')
    conn.commit()
    conn.close()


def save_user(telegram_id: int, username: str = None):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM users WHERE telegram_id = ?", (telegram_id,))
    if not cur.fetchone():
        cur.execute("INSERT INTO users (telegram_id, username, joined_at) VALUES (?, ?, ?)",
                    (telegram_id, username, datetime.now().isoformat()))
        conn.commit()
    conn.close()


def add_favorite(user_id: int, recipe_id: str, title: str):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO favorites (user_id, recipe_id, recipe_title, added_at) VALUES (?, ?, ?, ?)",
                (user_id, recipe_id, title, datetime.now().isoformat()))
    conn.commit()
    conn.close()


def get_favorites(user_id: int):
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT recipe_id, recipe_title FROM favorites WHERE user_id = ? ORDER BY added_at DESC", (user_id,))
    rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]


# ==================== СЛОВАРЬ ПЕРЕВОДА (русский → английский) ====================
RUSSIAN_TO_ENGLISH = {
    "пицца": "pizza",
    "курица": "chicken",
    "паста": "pasta",
    "торт": "cake",
    "суп": "soup",
    "салат": "salad",
    "говядина": "beef",
    "рыба": "fish",
    "рис": "rice",
    "свинина": "pork",
    "баранина": "lamb",
    "карри": "curry",
    "пирог": "pie",
    "бургер": "burger",
    "суши": "sushi",
    "десерт": "dessert",
    "блины": "pancakes",
    "оладьи": "pancakes",
    "печенье": "cookie",
    "мороженое": "ice cream",
    "шоколад": "chocolate",
    "яйца": "eggs",
    "сыр": "cheese",
    "овощи": "vegetables",
    "фрукты": "fruit",
    "борщ": "borscht",
    "оливье": "olivier salad",
    "плов": "plov",
    "хлеб": "bread",
    "мясо": "meat",
    "картошка": "potato",
    "картофель": "potato",
    "лук": "onion",
    "морковь": "carrot",
    "капуста": "cabbage",
    "свёкла": "beetroot",
    "помидоры": "tomato",
    "огурцы": "cucumber",
    "грибы": "mushrooms",
    "сырники": "syrniki",
    "вареники": "varenyky",
    "пельмени": "dumplings"
}


def translate_query_to_english(query: str) -> str:
    """Переводит русский запрос на английский"""
    query_lower = query.lower().strip()

    # Прямое совпадение
    if query_lower in RUSSIAN_TO_ENGLISH:
        return RUSSIAN_TO_ENGLISH[query_lower]

    # Проверка вхождения слова в словарь
    for ru_word, en_word in RUSSIAN_TO_ENGLISH.items():
        if ru_word in query_lower or query_lower in ru_word:
            return en_word

    return query_lower


# ==================== API TheMealDB ====================
# Кэш для результатов поиска
search_cache = {}


async def search_recipes(query: str):
    """Поиск рецептов через TheMealDB API"""
    # Проверяем кэш
    cache_key = f"search_{query}"
    if cache_key in search_cache:
        cache_time, results = search_cache[cache_key]
        if (datetime.now() - cache_time).seconds < 3600:  # Кэш на 1 час
            print(f"[КЭШ] Использованы результаты для: {query}")
            return results

    url = f"https://www.themealdb.com/api/json/v1/1/search.php?s={query}"
    print(f"[API] Запрос: {url}")

    timeout = aiohttp.ClientTimeout(total=10)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    print(f"[API] Ошибка статуса: {resp.status}")
                    return []

                data = await resp.json()
                meals = data.get("meals")

                if not meals:
                    print(f"[API] Рецепты не найдены для: {query}")
                    return []

                results = []
                for meal in meals:
                    results.append({
                        "id": meal["idMeal"],
                        "title": meal["strMeal"],
                        "image": meal["strMealThumb"]
                    })

                # Сохраняем в кэш
                search_cache[cache_key] = (datetime.now(), results)
                print(f"[API] Найдено рецептов: {len(results)}")
                return results

        except asyncio.TimeoutError:
            print(f"[API] Таймаут для запроса: {query}")
            return []
        except Exception as e:
            print(f"[API] Ошибка: {e}")
            return []


async def get_recipe_details(recipe_id: str):
    """Получение детальной информации о рецепте"""
    url = f"https://www.themealdb.com/api/json/v1/1/lookup.php?i={recipe_id}"
    timeout = aiohttp.ClientTimeout(total=10)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return None

                data = await resp.json()
                meal = data.get("meals", [None])[0]

                if not meal:
                    return None

                # Собираем ингредиенты
                ingredients = []
                for i in range(1, 21):
                    ingredient = meal.get(f"strIngredient{i}")
                    measure = meal.get(f"strMeasure{i}")
                    if ingredient and ingredient.strip():
                        ingredients.append(f"{measure} {ingredient}".strip())

                # Инструкция
                instructions = meal.get("strInstructions", "Инструкция не указана")
                if len(instructions) > 2000:
                    instructions = instructions[:2000] + "..."

                return {
                    "id": recipe_id,
                    "title": meal["strMeal"],
                    "category": meal.get("strCategory", "Не указана"),
                    "area": meal.get("strArea", "Не указана"),
                    "ingredients": ingredients[:15],
                    "instructions": instructions,
                    "youtube": meal.get("strYoutube", ""),
                    "source": meal.get("strSource", ""),
                    "image": meal.get("strMealThumb", "")
                }
        except Exception as e:
            print(f"[API] Ошибка получения деталей: {e}")
            return None


# ==================== ПЕРЕВОД НА РУССКИЙ ====================
translation_cache = {}


async def translate_to_russian(text: str) -> str:
    """Переводит текст на русский с кэшированием"""
    if not text:
        return ""

    cache_key = f"trans_{hash(text)}"
    if cache_key in translation_cache:
        cache_time, result = translation_cache[cache_key]
        if (datetime.now() - cache_time).seconds < 7200:
            return result

    try:
        if len(text) > 1500:
            text = text[:1500]
        translated = GoogleTranslator(source='auto', target='ru').translate(text)
        translation_cache[cache_key] = (datetime.now(), translated)
        return translated
    except Exception as e:
        print(f"[ПЕРЕВОД] Ошибка: {e}")
        return text


# ==================== ПАГИНАЦИЯ ====================
# Хранилище результатов поиска для каждого пользователя
user_results = {}


async def send_recipe_card(chat_id: int, recipe: dict, index: int, total: int):
    """Отправляет карточку одного рецепта"""
    russian_title = await translate_to_russian(recipe['title'])

    text = f"🍽 <b>{russian_title}</b>\n"
    text += f"📋 Рецепт {index + 1} из {total}"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⭐ В избранное", callback_data=f"fav_{recipe['id']}_{recipe['title'][:50]}"),
            InlineKeyboardButton(text="📖 Подробнее", callback_data=f"details_{recipe['id']}")
        ]
    ])

    if recipe.get('image'):
        try:
            await bot.send_photo(chat_id, recipe['image'], caption=text, parse_mode="HTML", reply_markup=kb)
        except:
            await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb)
    else:
        await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb)


async def send_page(chat_id: int, user_id: int, page: int):
    """Отправляет страницу с результатами (по 3 рецепта)"""
    data = user_results.get(user_id)
    if not data:
        return False

    recipes = data['recipes']
    items_per_page = 3
    total_pages = (len(recipes) + items_per_page - 1) // items_per_page

    start = page * items_per_page
    end = min(start + items_per_page, len(recipes))
    page_recipes = recipes[start:end]

    # Отправляем рецепты
    for i, recipe in enumerate(page_recipes):
        await send_recipe_card(chat_id, recipe, start + i, len(recipes))

    # Кнопки пагинации
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="◀ Назад", callback_data=f"page_{user_id}_{page - 1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton(text="Вперёд ▶", callback_data=f"page_{user_id}_{page + 1}"))

    if nav_buttons:
        nav_kb = InlineKeyboardMarkup(inline_keyboard=[nav_buttons])
        await bot.send_message(
            chat_id,
            f"📄 <b>Страница {page + 1} из {total_pages}</b>\n🔍 Найдено рецептов: {len(recipes)}",
            parse_mode="HTML",
            reply_markup=nav_kb
        )
    else:
        await bot.send_message(
            chat_id,
            f"✅ <b>Поиск завершён!</b>\n🔍 Найдено рецептов: {len(recipes)}",
            parse_mode="HTML",
            reply_markup=main_kb
        )
        # Очищаем результаты
        if user_id in user_results:
            del user_results[user_id]

    return True


# ==================== ОБРАБОТЧИКИ ====================
@dp.message(Command("start"))
async def cmd_start(message: Message):
    save_user(message.from_user.id, message.from_user.username)
    await message.answer(
        "👨‍🍳 <b>Привет! Я бот для поиска рецептов!</b>\n\n"
        "✨ <b>Что я умею:</b>\n"
        "• 🔍 Искать рецепты на русском и английском\n"
        "• 📖 Показывать полные рецепты с ингредиентами\n"
        "• ⭐ Сохранять рецепты в избранное\n\n"
        "📝 <b>Примеры запросов:</b>\n"
        "пицца, курица, паста, торт, суп, салат\n"
        "pizza, chicken, pasta, cake, soup, salad\n\n"
        "👇 <b>Нажми кнопку «Поиск рецепта»</b>",
        reply_markup=main_kb,
        parse_mode="HTML"
    )


@dp.message(F.text == "❓ Помощь")
async def cmd_help(message: Message):
    await cmd_start(message)


@dp.message(F.text == "🔍 Поиск рецепта")
async def search_button(message: Message, state: FSMContext):
    await state.set_state(SearchState.waiting_for_query)
    await message.answer(
        "🔍 <b>Введите название блюда</b>\n\n"
        "🍽 <b>На русском:</b> пицца, курица, паста, торт, суп, салат, борщ, оливье\n"
        "🇬🇧 <b>На английском:</b> pizza, chicken, pasta, cake, soup, salad\n\n"
        "❌ Для отмены нажмите «Отмена»",
        reply_markup=cancel_kb,
        parse_mode="HTML"
    )


@dp.message(F.text == "❌ Отмена")
async def cancel_search(message: Message, state: FSMContext):
    await state.clear()
    if message.from_user.id in user_results:
        del user_results[message.from_user.id]
    await message.answer("🔍 Поиск отменён.", reply_markup=main_kb)


@dp.message(SearchState.waiting_for_query)
async def process_search(message: Message, state: FSMContext):
    query = message.text.strip()
    if not query:
        return

    # Отправляем статус
    status_msg = await message.answer(f"🔍 <b>Ищу «{query}»...</b>", parse_mode="HTML")

    try:
        # Переводим запрос на английский, если нужно
        english_query = translate_query_to_english(query)

        if english_query != query.lower():
            await status_msg.edit_text(f"🔍 <b>Ищу «{query}»...</b>\n📝 Переведено: {query} → {english_query}",
                                       parse_mode="HTML")

        # Поиск рецептов
        recipes = await asyncio.wait_for(search_recipes(english_query), timeout=15)

        await status_msg.delete()

        if not recipes:
            await message.answer(
                f"❌ <b>«{query}» — ничего не найдено</b>\n\n"
                "✅ <b>Попробуйте другие запросы:</b>\n"
                "пицца, курица, паста, торт, суп, салат, борщ, оливье, плов\n"
                "pizza, chicken, pasta, cake, soup, salad, beef, fish, rice\n\n"
                "💡 <b>Совет:</b> Используйте более простые названия!",
                reply_markup=main_kb,
                parse_mode="HTML"
            )
            await state.clear()
            return

        # Сохраняем результаты
        user_results[message.from_user.id] = {
            'recipes': recipes,
            'query': query,
            'english_query': english_query
        }

        # Показываем первую страницу
        await send_page(message.chat.id, message.from_user.id, 0)
        await state.clear()

    except asyncio.TimeoutError:
        await status_msg.delete()
        await message.answer(
            "❌ <b>Превышено время ожидания</b>\n\n"
            "Пожалуйста, попробуйте ещё раз через несколько секунд.",
            reply_markup=main_kb,
            parse_mode="HTML"
        )
        await state.clear()
    except Exception as e:
        await status_msg.delete()
        print(f"[ОШИБКА] {e}")
        await message.answer(
            "❌ <b>Произошла ошибка при поиске</b>\n\n"
            "Пожалуйста, попробуйте позже.",
            reply_markup=main_kb,
            parse_mode="HTML"
        )
        await state.clear()


@dp.callback_query(F.data.startswith("page_"))
async def pagination_callback(callback: CallbackQuery):
    try:
        _, user_id_str, page_str = callback.data.split("_")
        user_id = int(user_id_str)
        page = int(page_str)

        if callback.from_user.id != user_id:
            await callback.answer("❌ Это не ваши результаты!", show_alert=True)
            return

        if user_id not in user_results:
            await callback.answer("❌ Результаты поиска устарели. Начните новый поиск.", show_alert=True)
            return

        await callback.answer()

        try:
            await callback.message.delete()
        except:
            pass

        await send_page(callback.message.chat.id, user_id, page)

    except Exception as e:
        print(f"[ОШИБКА] {e}")
        await callback.answer("❌ Ошибка", show_alert=True)


@dp.callback_query(F.data.startswith("fav_"))
async def favorite_callback(callback: CallbackQuery):
    try:
        parts = callback.data.split("_", 2)
        recipe_id = parts[1]
        title = parts[2] if len(parts) > 2 else "Рецепт"

        add_favorite(callback.from_user.id, recipe_id, title)
        await callback.answer("⭐ Рецепт добавлен в избранное!", show_alert=False)
    except Exception as e:
        print(f"[ОШИБКА] {e}")
        await callback.answer("❌ Ошибка", show_alert=False)


@dp.callback_query(F.data.startswith("details_"))
async def details_callback(callback: CallbackQuery):
    await callback.answer("📖 Загружаю рецепт...")

    recipe_id = callback.data.replace("details_", "")

    try:
        details = await asyncio.wait_for(get_recipe_details(recipe_id), timeout=12)

        if not details:
            await callback.message.answer("❌ Не удалось загрузить рецепт. Попробуйте другой.")
            return

        # Переводим на русский
        russian_title = await translate_to_russian(details['title'])
        russian_category = await translate_to_russian(details['category'])
        russian_area = await translate_to_russian(details['area'])
        russian_instructions = await translate_to_russian(details['instructions'])

        # Собираем текст
        text = f"<b>{russian_title}</b>\n\n"
        text += f"📌 <b>Категория:</b> {russian_category}\n"
        text += f"🌍 <b>Кухня:</b> {russian_area}\n\n"

        text += "📝 <b>Ингредиенты:</b>\n"
        for ing in details['ingredients'][:12]:
            russian_ing = await translate_to_russian(ing)
            text += f"• {russian_ing}\n"

        text += f"\n👨‍🍳 <b>Приготовление:</b>\n{russian_instructions}\n"

        # Кнопки
        buttons = []
        if details.get('source'):
            buttons.append([InlineKeyboardButton(text="🔗 Оригинальный рецепт", url=details['source'])])
        if details.get('youtube'):
            buttons.append([InlineKeyboardButton(text="🎥 Видео на YouTube", url=details['youtube'])])
        buttons.append(
            [InlineKeyboardButton(text="⭐ В избранное", callback_data=f"fav_{recipe_id}_{details['title'][:50]}")])

        reply_markup = InlineKeyboardMarkup(inline_keyboard=buttons)

        # Отправляем
        if len(text) <= 4000:
            await callback.message.answer(text, parse_mode="HTML", reply_markup=reply_markup)
        else:
            parts = [text[i:i + 4000] for i in range(0, len(text), 4000)]
            for i, part in enumerate(parts):
                markup = reply_markup if i == len(parts) - 1 else None
                await callback.message.answer(part, parse_mode="HTML", reply_markup=markup)

    except asyncio.TimeoutError:
        await callback.message.answer("❌ Превышено время ожидания. Попробуйте другой рецепт.")
    except Exception as e:
        print(f"[ОШИБКА] {e}")
        await callback.message.answer("❌ Ошибка загрузки рецепта")


@dp.message(F.text == "⭐ Мои избранные")
async def show_favorites(message: Message):
    favorites = get_favorites(message.from_user.id)

    if not favorites:
        await message.answer(
            "⭐ <b>У вас пока нет избранных рецептов</b>\n\n"
            "Чтобы добавить рецепт в избранное, нажмите кнопку «⭐ В избранное» под любым рецептом.",
            parse_mode="HTML"
        )
        return

    text = "⭐ <b>Ваши избранные рецепты:</b>\n\n"
    for fav in favorites[:20]:
        text += f"• {fav['recipe_title']}\n"

    text += f"\n<b>Всего:</b> {len(favorites)} рецептов"
    await message.answer(text, parse_mode="HTML")


@dp.message(F.text == "🏆 Топ популярных")
async def show_top(message: Message):
    await message.answer(
        "🏆 <b>Топ популярных рецептов</b>\n\n"
        "Функция будет доступна после накопления статистики.\n\n"
        "💡 <b>Что можно делать прямо сейчас:</b>\n"
        "• 🔍 Искать рецепты через «Поиск рецепта»\n"
        "• ⭐ Добавлять понравившиеся в избранное\n"
        "• 📖 Смотреть подробные инструкции приготовления",
        parse_mode="HTML"
    )


# ==================== ЗАПУСК ====================
async def main():
    init_db()
    print("=" * 55)
    print("🍽 БОТ ДЛЯ ПОИСКА РЕЦЕПТОВ - ЗАПУЩЕН 🍽")
    print("=" * 55)
    print("✨ Функционал:")
    print("   • Поиск на русском (пицца, курица, паста, торт, суп, салат, борщ, оливье, плов...)")
    print("   • Поиск на английском (pizza, chicken, pasta, cake, soup, salad...)")
    print("   • Пагинация (по 3 рецепта на страницу)")
    print("   • Кэширование результатов")
    print("   • Избранное")
    print("=" * 55)
    print("📝 Тестовый запрос: пицца")
    print("=" * 55)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())