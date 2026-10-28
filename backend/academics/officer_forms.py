from django import forms
from django.core.exceptions import ValidationError

from academics.models import Unit
from locations.models import Building, Floor, Room
from scheduling.models import Examination, ExaminationPeriod


class ExaminationPeriodForm(forms.ModelForm):
    class Meta:
        model = ExaminationPeriod
        fields = ["name", "academic_year", "semester", "start_date", "end_date"]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
        }

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")
        if start_date and end_date and end_date < start_date:
            raise ValidationError("The end date must be on or after the start date.")
        return cleaned_data


class ExaminationForm(forms.ModelForm):
    class Meta:
        model = Examination
        fields = ["period", "unit", "examination_type", "duration_minutes"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["period"].queryset = ExaminationPeriod.objects.order_by("-start_date")
        self.fields["unit"].queryset = Unit.objects.filter(status="ACTIVE").order_by("code")
        self.fields["duration_minutes"].min_value = 1

    def clean(self):
        cleaned_data = super().clean()
        period = cleaned_data.get("period")
        unit = cleaned_data.get("unit")
        examination_type = cleaned_data.get("examination_type")
        existing = Examination.objects.filter(
            period=period,
            unit=unit,
            examination_type=examination_type,
        ) if period and unit and examination_type else Examination.objects.none()
        if self.instance.pk:
            existing = existing.exclude(pk=self.instance.pk)
        if existing.exists():
            raise ValidationError("An examination of this type already exists for this unit and period.")
        return cleaned_data


class RoomForm(forms.ModelForm):
    class Meta:
        model = Room
        fields = [
            "building",
            "floor",
            "room_number",
            "name",
            "capacity",
            "room_type",
            "description",
            "status",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["building"].queryset = Building.objects.order_by("name")
        self.fields["floor"].queryset = Floor.objects.select_related("building").order_by(
            "building__name", "floor_number"
        )
        self.fields["capacity"].min_value = 1

    def clean(self):
        cleaned_data = super().clean()
        building = cleaned_data.get("building")
        floor = cleaned_data.get("floor")
        room_number = cleaned_data.get("room_number")
        if building and floor and floor.building_id != building.id:
            self.add_error("floor", "Select a floor in the selected building.")
        if building and room_number:
            existing = Room.objects.filter(building=building, room_number=room_number)
            if self.instance.pk:
                existing = existing.exclude(pk=self.instance.pk)
            if existing.exists():
                self.add_error("room_number", "This room number is already used in the selected building.")
        return cleaned_data
