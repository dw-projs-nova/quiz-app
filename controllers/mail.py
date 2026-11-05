from email.message import EmailMessage
from smtplib import SMTP, SMTP_SSL

from flask import current_app, render_template


def send_smtp_message(message):
    smtp_class = SMTP_SSL if current_app.config["SMTP_USE_SSL"] else SMTP
    with smtp_class(
        current_app.config["SMTP_HOST"],
        current_app.config["SMTP_PORT"],
        timeout=15,
    ) as smtp:
        if current_app.config["SMTP_STARTTLS"]:
            smtp.starttls()
        if current_app.config["SMTP_USERNAME"]:
            smtp.login(
                current_app.config["SMTP_USERNAME"],
                current_app.config["SMTP_PASSWORD"] or "",
            )
        smtp.send_message(message)


def send_login_email(email, login_url):
    message = EmailMessage()
    message["Subject"] = "Sign in to Quiz App"
    message["From"] = current_app.config["SMTP_FROM"]
    message["To"] = email
    message.set_content(
        f"Sign in to Quiz App using this link:\n\n{login_url}\n\n"
        "This link expires after 30 minutes.\n"
        "If you did not request this email, you can safely ignore it."
    )
    message.add_alternative(
        render_template("mail/login.html", login_url=login_url),
        subtype="html",
    )
    send_smtp_message(message)
