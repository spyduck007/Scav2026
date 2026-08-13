from django import forms
from django.contrib import admin

from .models import (
    Challenge,
    ChallengeCategory,
    ChallengeDependency,
    ChallengeSolve,
    DiscordSettings,
    Participant,
)


class ChallengeDependencyInline(admin.TabularInline):
    model = ChallengeDependency
    fk_name = "challenge"
    extra = 1
    autocomplete_fields = ("prerequisite",)


class ChallengeAdminForm(forms.ModelForm):
    class Meta:
        model = Challenge
        fields = "__all__"

    def clean(self):
        cleaned = super().clean()
        challenge_type = cleaned.get("challenge_type")
        decay = cleaned.get("decay_percent")
        prerequisites = cleaned.get("prerequisites")

        if challenge_type != Challenge.ChallengeType.DECREASING and decay and decay > 0:
            self.add_error(
                "decay_percent",
                "Decay percentage applies only to decreasing challenges.",
            )

        if challenge_type == Challenge.ChallengeType.DECREASING and (
            decay is None or decay <= 0
        ):
            self.add_error(
                "decay_percent",
                "Decreasing challenges must specify a positive decay percentage.",
            )

        if challenge_type != Challenge.ChallengeType.DEPENDENT and prerequisites:
            self.add_error(
                "prerequisites",
                "Only dependent challenges may include prerequisites.",
            )

        return cleaned


@admin.register(ChallengeCategory)
class ChallengeCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "sort_order", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("name", "description")
    prepopulated_fields = {"slug": ("name",)}
    ordering = ("sort_order", "name")


@admin.register(Challenge)
class ChallengeAdmin(admin.ModelAdmin):
    form = ChallengeAdminForm
    list_display = (
        "title",
        "category",
        "challenge_type",
        "base_points",
        "is_active",
        "updated_at",
    )
    list_filter = ("challenge_type", "category", "is_active")
    search_fields = ("title", "description")
    autocomplete_fields = ("category", "prerequisites")
    prepopulated_fields = {"slug": ("title",)}
    inlines = (ChallengeDependencyInline,)


@admin.register(ChallengeSolve)
class ChallengeSolveAdmin(admin.ModelAdmin):
    list_display = (
        "challenge",
        "team_year",
        "participant",
        "awarded_points",
        "created_at",
    )
    list_filter = ("team_year", "challenge__category", "challenge__challenge_type")
    search_fields = (
        "challenge__title",
        "participant__display_name",
        "participant__ion_username",
    )
    autocomplete_fields = ("challenge", "participant")


@admin.register(Participant)
class ParticipantAdmin(admin.ModelAdmin):
    list_display = (
        "ion_username",
        "display_name",
        "graduation_year",
        "is_admin",
        "is_scavcomm",
        "last_login",
    )
    search_fields = ("ion_username", "display_name", "email")
    list_filter = ("is_admin", "is_scavcomm", "graduation_year")


@admin.register(DiscordSettings)
class DiscordSettingsAdmin(admin.ModelAdmin):
    list_display = ("notifications_enabled", "webhook_url", "updated_at")
    fieldsets = (
        (
            "Discord Notification Settings",
            {
                "fields": ("notifications_enabled", "webhook_url"),
                "description": "Configure Discord first blood notifications. When enabled, "
                "a notification will be sent to the Discord webhook URL whenever "
                "a challenge is solved for the first time.",
            },
        ),
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
