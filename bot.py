"""
polytechfizrabot (2019)
celangau.git <at> yandex.uz

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""

from datetime import datetime
from enum import Enum
import configparser
import csv
import hashlib
import inspect
import io
import logging
import telebot
import os.path
import requests
import time

# Режимы поиска студента
class SearchMode(Enum):
    NAME = 1
    GROUP = 2

    @staticmethod
    def from_str(label):
        if label == "name":
            return SearchMode.NAME
        elif label == "group":
            return SearchMode.GROUP
        else:
            raise NotImplementedError


# КОНСТАНТЫ
# Название программы
C_LOGGER_NAME = "polytechfizrabot"
# Форматирование каждой строки лога
C_LOGGER_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"
# Путь к файлу с конфигурацией
C_CONFIG_PATH = "ini/config.ini"
# Путь к файлу с историей поиска
C_USER_HISTORY_PATH = "ini/user_history.ini"
# Пропуск первых 10 столбцов таблицы при подсчёте посещений:
# Имя, группа, курс, факультет, куратор, зачёт летняя, группа здоровья,
# баллы за посещения, баллы за реферат, количество посещений
C_CSV_SKIP_COLUMNS = 10
# Максимальное количество человек, отображаемых в результатах поиска
C_CSV_MAX_STUDENTS = 30
# Минимальная длина поискового запроса
C_MIN_QUERY_LENGTH = 3
# Максимальная длина поискового запроса
C_MAX_QUERY_LENGTH = 100
# Максимальное время жизни кешированного CSV в секундах
C_MAX_CACHE_TIME = 2 * 60 * 60  # 2 часа
# Пауза, выжидаемая перед перезапуском упавшего бота
C_BOT_RESTART_PAUSE = 5

# Настройка логирования
log = logging.getLogger(C_LOGGER_NAME)
log.setLevel(logging.DEBUG)
console_logger = logging.StreamHandler()
console_logger.setLevel(logging.DEBUG)
console_logger.setFormatter(logging.Formatter(C_LOGGER_FORMAT))
log.addHandler(console_logger)

# Чтение конфигурации
config = configparser.ConfigParser()
config.read(C_CONFIG_PATH)
csv_url = config["General"]["CsvUrl"]
csv_path = config["General"]["CsvPath"]

# Чтение истории запросов пользователей
user_history = configparser.ConfigParser()
user_history.read(C_USER_HISTORY_PATH)

# Бот
telegram = telebot.TeleBot(config["General"]["TelegramBotToken"])

# CSV удерживается в памяти, чтобы при каждом поиске не считывать его с диска
# При запуске бота в память читается сохранённый на диске CSV-файл
current_csv = ""
if os.path.exists(csv_path):
    with open(csv_path, "r") as csv_file:
        current_csv = csv_file.read()
        log.info("CSV-файл успешно загружен с диска")
else:
    log.warn("CSV-файл не существует, он будет загружен при первом обновлении")

# Функция-фильтр для поиска студента в базе
def filter_student(search_mode, entry, query):
    # При поиске по имени проверяется наличие поискового
    # запроса в ФИО вне зависимости от регистра обеих строк
    if search_mode == SearchMode.NAME:
        entry = entry[0].lower()
        return query.lower() in entry
    # При поиске по группе проверяется точное соответствие
    elif search_mode == SearchMode.GROUP:
        return entry[1] == query
    else:
        return False


# Сохраняет объект с содержимым ini файла в заданный файл
def save_ini(ini, path):
    try:
        with open(path, "w") as config_file:
            ini.write(config_file)
    except:
        log.error(
            "Невозможно сохранить ini файл, все изменения остаются в памяти",
            exc_info=True,
        )


# Проверяет необходимость обновления локального CSV файла
def check_csv():
    global current_csv

    loaded_at = datetime.fromtimestamp(
        int(config["General"]["CsvLoadedAt"])
        if "CsvLoadedAt" in config["General"]
        else 0
    )
    now = datetime.now()

    time_delta = int((now - loaded_at).total_seconds())
    if time_delta >= C_MAX_CACHE_TIME:
        log.info(
            "Запуск обновления (последнее было {} секунд назад)".format(time_delta)
        )

        response = None
        try:
            response = requests.get(csv_url)
            if response.status_code != 200:
                raise Exception(
                    "Получен HTTP-код {}, ожидался 200".format(response.status_code)
                )
        except:
            log.error("Ошибка при обновлении CSV", exc_info=True)
            return

        loaded_csv = response.content
        loaded_at = str(int(datetime.timestamp(datetime.now())))
        config["General"]["CsvLoadedAt"] = loaded_at

        # Новый CSV может быть успешно загружен, но не всегда в нём будут изменения.
        # Для их обнаружения используется сравнение хешей содержимого
        old_hash = hashlib.sha256(current_csv.encode()).hexdigest()
        new_hash = hashlib.sha256(loaded_csv).hexdigest()
        if old_hash == new_hash:
            log.info("В загруженном CSV нет изменений")
        else:
            try:
                current_csv = loaded_csv.decode("utf-8")
                with open(csv_path, "w+b") as csv_file:
                    csv_file.write(loaded_csv)
                config["General"]["CsvUpdatedAt"] = loaded_at
                log.info("Обновлённый CSV успешно сохранён")
            except:
                log.error(
                    "Ошибка при сохранении обновлённого CSV", exc_info=True,
                )

        save_ini(config, C_CONFIG_PATH)


# Генерирует сообщение с посещаемостью занятий, при необходимости
# сохраняет успешный поисковый запрос в историю
def handle_attendance(id, mode, query):
    check_csv()

    attendance_message = []
    try:
        reader = csv.reader(current_csv.splitlines())
        for row in [x for x in reader if filter_student(mode, x, query)]:
            # Посещения = число непустых ячеек после столбца №C_CSV_SKIP_COLUMNS
            attendance = len([e for e in row[C_CSV_SKIP_COLUMNS:] if len(e) > 0])
            attendance_message.append(
                "{} (группа {})\nПосещений: {}".format(row[0], row[1], attendance)
            )
            if len(attendance_message) == C_CSV_MAX_STUDENTS:
                break
    except:
        log.error("Ошибка при поиске в CSV", exc_info=True)
    attendance_message = "\n\n".join(attendance_message)

    has_attendace = len(attendance_message) > 0
    if not has_attendace:
        attendance_message = "По вашему запросу ничего не найдено"

    attendance_message += "\n\nПоследнее обновление:\n{} UTC".format(
        datetime.fromtimestamp(int(config["General"]["CsvUpdatedAt"])).strftime(
            "%Y-%m-%d, %H:%M:%S"
        )
    )
    telegram.send_message(id, attendance_message)

    if has_attendace:
        user_history[str(id)] = {"mode": str(mode.value), "query": query}
        save_ini(user_history, C_USER_HISTORY_PATH)


@telegram.message_handler(commands=["start", "help"])
def handle_start(msg):
    telegram.send_message(
        msg.chat.id,
        inspect.cleandoc(config["General"]["HelpMessage"]),
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )


@telegram.message_handler(commands=["name", "group"])
def handle_search(msg):
    if " " not in msg.text:
        telegram.send_message(msg.chat.id, "Не указан поисковый запрос")
        return

    mode = SearchMode.from_str(msg.text[1 : msg.text.find(" ")])
    query = msg.text[msg.text.find(" ") + 1 :]
    if not C_MIN_QUERY_LENGTH < len(query) < C_MAX_QUERY_LENGTH:
        telegram.send_message(msg.chat.id, "Некорректная длина поискового запроса")
        return

    handle_attendance(msg.chat.id, mode, query)


@telegram.message_handler(commands=["forget"])
def handle_forget(msg):
    if user_history.remove_section(str(msg.chat.id)):
        telegram.send_message(
            msg.chat.id, "Ваш последний поисковый запрос успешно забыт"
        )
        save_ini(user_history, C_USER_HISTORY_PATH)
    else:
        telegram.send_message(msg.chat.id, "Вы раньше ничего не искали")


@telegram.message_handler(commands=["check"])
def handle_check(msg):
    id = str(msg.chat.id)

    if id not in user_history:
        telegram.send_message(msg.chat.id, "Вы раньше ничего не искали")
        return

    mode = SearchMode(int(user_history[id]["mode"]))
    query = user_history[id]["query"]
    handle_attendance(msg.chat.id, mode, query)


while True:
    try:
        log.info("Запуск бота")
        telegram.polling()
        break  # Срабатывает при нормальном завершении работы
    except:
        log.error("Ошибка бота", exc_info=True)
        log.info("Ожидание {} секунд перед перезапуском...".format(C_BOT_RESTART_PAUSE))
        time.sleep(C_BOT_RESTART_PAUSE)
