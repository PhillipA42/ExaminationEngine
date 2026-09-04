from rest_framework import serializers
from .models import Campus, Building, Floor, Room

class CampusSerializer(serializers.ModelSerializer):
    class Meta:
        model = Campus
        fields = '__all__'

class BuildingSerializer(serializers.ModelSerializer):
    campus_name = serializers.ReadOnlyField(source='campus.name')

    class Meta:
        model = Building
        fields = '__all__'

class FloorSerializer(serializers.ModelSerializer):
    building_name = serializers.ReadOnlyField(source='building.name')

    class Meta:
        model = Floor
        fields = '__all__'

class RoomSerializer(serializers.ModelSerializer):
    building_name = serializers.ReadOnlyField(source='building.name')
    floor_name = serializers.ReadOnlyField(source='floor.name')

    class Meta:
        model = Room
        fields = '__all__'