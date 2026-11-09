from django.contrib import admin

from inventory.models import DrugClass, Medicine

admin.site.register(DrugClass)
admin.site.register(Medicine)
