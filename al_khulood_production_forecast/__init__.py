# -*- coding: utf-8 -*-
from . import models
from .models.demand_event import seed_demand_events


def post_init_hook(env):
    """Ship the known Hijri and Gregorian calendar once, at install."""
    seed_demand_events(env)
