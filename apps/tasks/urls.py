from django.urls import path

from . import views

urlpatterns = [
    path("tasks/", views.task_list, name="tasks"),
    path("tasks/new/", views.task_create, name="task-create"),
    path("tasks/<uuid:task_id>/status/", views.task_status, name="task-status"),
]
