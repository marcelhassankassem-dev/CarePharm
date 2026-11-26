from django.urls import path

from assistant import views

urlpatterns = [
    path("", views.chat, name="assistant_chat"),
    path("start-over/", views.start_over, name="assistant_start_over"),
]
