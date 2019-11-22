# polytechfizrabot (2019)
# celangau.git <at> yandex.uz
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from datetime import datetime
from enum import Enum
import configparser
import csv
import inspect
import logging
import telebot
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
# Пропуск первых 8 столбцов таблицы при подсчёте посещений
# 1 Имя			2 Группа			3 Курс			4 Факультет
# 5 Куратор		6 Зачёт летняя		7 Гр. здоровья	8 Кол-во посещений
C_CSV_SKIP_COLUMNS = 8
# Максимальное количество человек, отображаемых в результатах поиска
C_CSV_MAX_STUDENTS = 30
# Минимальная длина поискового запроса
C_MIN_QUERY_LENGTH = 3
# Максимальная длина поискового запроса
C_MAX_QUERY_LENGTH = 100
# Максимальное время жизни кешированного CSV в секундах
C_MAX_CACHE_TIME = 2 * 60 * 60 # 2 часа
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
		log.error("Невозможно сохранить ini файл, все изменения остаются в памяти")


# Проверяет необходимость обновления локального CSV файла
def check_csv():
	updated_at = datetime.fromtimestamp(int(config["General"]["CsvUpdatedAt"])) if "CsvUpdatedAt" in config["General"] else 0
	now = datetime.now()
	
	if int((now - updated_at).total_seconds()) >= C_MAX_CACHE_TIME:
		with open(csv_path, "w+b") as csv_file:
			response = requests.get(csv_url)
			if response.status_code != 200:
				log.error("Невозможно обновить CSV, получен код {}".format(response.status_code))
			else:
				try:
					csv_file.write(response.content)
					updated_at = str(int(datetime.timestamp(datetime.now())))
					config["General"]["CsvUpdatedAt"] = updated_at
					save_ini(config, C_CONFIG_PATH)
					log.info("CSV успешно обновлён")
				except:
					log.error("Невозможно обновить CSV, ошибка при сохранении данных")


# Генерирует сообщение с посещаемостью занятий, при необходимости 
# сохраняет успешный поисковый запрос в историю
def handle_attendance(id, mode, query):
	check_csv()
	
	attendance_message = []
	try:
		with open(csv_path) as csv_file:
			reader = csv.reader(csv_file)
			for row in [x for x in reader if filter_student(mode, x, query)]:
				# Посещения = число непустых ячеек после столбца №C_CSV_SKIP_COLUMNS
				attendance = len([e for e in row[C_CSV_SKIP_COLUMNS:] if len(e) > 0])
				attendance_message.append("{} (группа {})\nПосещений: {}".format(row[0], row[1], attendance))
				if len(attendance_message) == C_CSV_MAX_STUDENTS:
					break
	except:
		log.error("Ошибка при поиске в CSV")
	attendance_message = "\n\n".join(attendance_message)
	
	has_attendace = len(attendance_message) > 0
	if not has_attendace:
		attendance_message = "По вашему запросу ничего не найдено"
	
	attendance_message += "\n\nПоследнее обновление:\n{} UTC".format(
		datetime.fromtimestamp(int(config["General"]["CsvUpdatedAt"]))
				.strftime("%Y-%m-%d, %H:%M:%S")
	)
	telegram.send_message(id, attendance_message)
	
	if has_attendace:
		user_history[str(id)] = {
			"mode": str(mode.value),
			"query": query
		}
		save_ini(user_history, C_USER_HISTORY_PATH)


@telegram.message_handler(commands=["start", "help"])
def handle_start(msg):
	telegram.send_message(
		msg.chat.id,
		inspect.cleandoc(config["General"]["HelpMessage"]),
		parse_mode = "Markdown",
		disable_web_page_preview = True
	)


@telegram.message_handler(commands=["name", "group"])
def handle_search(msg):
	if " " not in msg.text:
		telegram.send_message(msg.chat.id, "Не указан поисковый запрос")
		return
	
	mode = SearchMode.from_str(msg.text[1:msg.text.find(" ")])
	query = msg.text[msg.text.find(" ") + 1:]
	if not C_MIN_QUERY_LENGTH < len(query) < C_MAX_QUERY_LENGTH:
		telegram.send_message(msg.chat.id, "Некорректная длина поискового запроса")
		return
	
	check_csv()
	handle_attendance(msg.chat.id, mode, query)


@telegram.message_handler(commands=["forget"])
def handle_forget(msg):
	if user_history.remove_section(str(msg.chat.id)):
		telegram.send_message(msg.chat.id, "Ваш последний поисковый запрос успешно забыт")
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
		break # Срабатывает при нормальном завершении работы
	except Exception as e:
		log.error("Ошибка бота", e)
		log.info("Ожидание {} секунд перед перезапуском...".format(C_BOT_RESTART_PAUSE))
		time.sleep(C_BOT_RESTART_PAUSE)
