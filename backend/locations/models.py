from django.db import models

class Campus(models.Model):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    status = models.CharField(max_length=20, default='ACTIVE')

    def __str__(self):
        return f"{self.code} - {self.name}"

class Building(models.Model):
    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, related_name='buildings')
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    status = models.CharField(max_length=20, default='ACTIVE')

    def __str__(self):
        return f"{self.name} ({self.campus.code})"

class Floor(models.Model):
    building = models.ForeignKey(Building, on_delete=models.CASCADE, related_name='floors')
    floor_number = models.IntegerField(default=0) # 0 for Ground Floor, 1 for 1st Floor
    name = models.CharField(max_length=100) # e.g., "Ground Floor", "First Floor"
    description = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"{self.building.name} - {self.name}"

class Room(models.Model):
    floor = models.ForeignKey(Floor, on_delete=models.CASCADE, related_name='rooms')
    building = models.ForeignKey(Building, on_delete=models.CASCADE, related_name='rooms')
    room_number = models.CharField(max_length=50)
    name = models.CharField(max_length=255) # e.g., "SC-101" or "Main Hall A"
    capacity = models.IntegerField()
    room_type = models.CharField(max_length=50, default='LECTURE_HALL') # LECTURE_HALL, LAB, AUDITORIUM
    description = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=20, default='ACTIVE')

    def __str__(self):
        return f"{self.name} (Cap: {self.capacity})"