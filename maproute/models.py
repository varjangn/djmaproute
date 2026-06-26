from django.db import models

class FuelStation(models.Model):
    opis_id = models.IntegerField(db_index=True, db_column='opis_truckstop_id')
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=50)
    rack_id = models.IntegerField()
    retail_price = models.DecimalField(max_digits=10, decimal_places=4)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)

    def __str__(self):
        return f"{self.name} - {self.city}, {self.state} (${self.retail_price})"
