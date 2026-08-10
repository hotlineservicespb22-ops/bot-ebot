@echo off
title Hotline Service Bot Starter
chcp 65001 > nul

echo ===================================================
echo     Запуск бота сервисной службы Hotline Service
echo ===================================================

:: Проверка наличия Python
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ОШИБКА] Python не найден в системе. Пожалуйста, установите Python.
    pause
    exit /b 1
)

:: Переход в директорию скрипта
cd /d "%~dp0"

:: Создание виртуального окружения, если оно не существует
if not exist .venv (
    echo [ИНФО] Создание виртуального окружения .venv...
    python -m venv .venv
    if %errorlevel% neq 0 (
        echo [ОШИБКА] Не удалось создать виртуальное окружение.
        pause
        exit /b 1
    )
)

:: Активация виртуального окружения
echo [ИНФО] Активация виртуального окружения...
call .venv\Scripts\activate.bat
if %errorlevel% neq 0 (
    echo [ОШИБКА] Не удалось активировать виртуальное окружение.
    pause
    exit /b 1
)

:: Установка зависимостей
echo [ИНФО] Проверка и установка зависимостей...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [ОШИБКА] Не удалось установить необходимые библиотеки.
    pause
    exit /b 1
)

:: Запуск бота
echo [ИНФО] Запуск бота...
python -m bot.main

pause
