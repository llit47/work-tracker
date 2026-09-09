from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    ApplicationSetting,
    LocationDisplayName,
    LocationTimezone,
    WorkEvent,
    WorkEventCorrection,
)

APPLICATION_SETTINGS_ID = 1
DEFAULT_APPLICATION_TITLE = "Work Tracker"
DEFAULT_LOCATION = "gabinet_zabki"


@dataclass(frozen=True)
class LocationPresentation:
    location: str
    display_name: str | None
    timezone: str | None


@dataclass(frozen=True)
class ApplicationPresentationSettings:
    application_title: str
    locations: tuple[LocationPresentation, ...]


def load_application_settings(session: Session) -> ApplicationPresentationSettings:
    stored_settings = session.get(ApplicationSetting, APPLICATION_SETTINGS_ID)
    aliases = {
        alias.location: alias.display_name
        for alias in session.scalars(
            select(LocationDisplayName).order_by(LocationDisplayName.location.asc())
        )
    }
    timezones = {
        setting.location: setting.timezone
        for setting in session.scalars(
            select(LocationTimezone).order_by(LocationTimezone.location.asc())
        )
    }
    known_locations = {DEFAULT_LOCATION, *aliases, *timezones}
    known_locations.update(session.scalars(select(WorkEvent.location).distinct()))
    known_locations.update(
        location
        for location in session.scalars(
            select(WorkEventCorrection.location)
            .where(WorkEventCorrection.location.is_not(None))
            .distinct()
        )
        if location is not None
    )
    return ApplicationPresentationSettings(
        application_title=(
            stored_settings.application_title
            if stored_settings is not None
            else DEFAULT_APPLICATION_TITLE
        ),
        locations=tuple(
            LocationPresentation(
                location=location,
                display_name=aliases.get(location),
                timezone=timezones.get(location),
            )
            for location in sorted(known_locations)
        ),
    )
