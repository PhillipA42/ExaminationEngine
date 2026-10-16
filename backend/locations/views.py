from rest_framework import viewsets, permissions
from .models import Campus, Building, Floor, Room
from .serializers import CampusSerializer, BuildingSerializer, FloorSerializer, RoomSerializer

class CampusViewSet(viewsets.ModelViewSet):
    queryset = Campus.objects.all()
    serializer_class = CampusSerializer
    permission_classes = [permissions.IsAuthenticated]

class BuildingViewSet(viewsets.ModelViewSet):
    queryset = Building.objects.all()
    serializer_class = BuildingSerializer
    permission_classes = [permissions.IsAuthenticated]

class FloorViewSet(viewsets.ModelViewSet):
    queryset = Floor.objects.all()
    serializer_class = FloorSerializer
    permission_classes = [permissions.IsAuthenticated]

class RoomViewSet(viewsets.ModelViewSet):
    queryset = Room.objects.all()
    serializer_class = RoomSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        """
        Allows optional filtering of rooms by building_id 
        via URL query parameter: /api/locations/rooms/?building_id=1
        """
        queryset = Room.objects.all()
        building_id = self.request.query_params.get('building_id', None)
        if building_id is not None:
            queryset = queryset.filter(building_id=building_id)
        return queryset