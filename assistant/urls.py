from django.urls import path

from assistant import views

urlpatterns = [
    path("", views.chat, name="assistant_chat"),
    path("start-over/", views.start_over, name="assistant_start_over"),
    path("questions/", views.my_questions, name="my_questions"),
    path("messages/", views.messages_page, name="messages_page"),
    path("messages/<int:question_id>/", views.messages_page, name="message_question"),
    path("messages/<int:question_id>/reply/", views.message_reply, name="message_reply"),
]
