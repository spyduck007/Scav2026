"""Database models for the Scavenger Hunt application."""

from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4

from django.db import models
from django.db.models import Max
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.utils.text import slugify


class Participant(models.Model):
    """Represents a user authenticated through Ion."""

    ion_username = models.CharField(max_length=64, unique=True)
    display_name = models.CharField(max_length=255, blank=True)
    email = models.EmailField(blank=True)
    graduation_year = models.PositiveIntegerField(null=True, blank=True)
    is_admin = models.BooleanField(default=False)
    is_scavcomm = models.BooleanField(
        default=False,
        help_text="Scavcomm members can view analytics but cannot access Django admin.",
    )
    last_login = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["ion_username"]

    def __str__(self) -> str:
        return self.display_name or self.ion_username

    def can_view_analytics(self) -> bool:
        """Returns True if the user can access the analytics page."""
        return self.is_admin or self.is_scavcomm

    def can_bypass_hunt_window(self) -> bool:
        """Returns True if the user can access the site outside the hunt window."""
        return self.is_admin or self.is_scavcomm


class ChallengeCategory(models.Model):
    """Groups challenges under a shared theme for easier navigation."""

    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=150, unique=True, blank=True)
    description = models.TextField(blank=True)
    sort_order = models.PositiveIntegerField(
        default=0, help_text="Lower numbers appear first."
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "name"]

    def _generate_unique_slug(self) -> str:
        base_slug = slugify(self.name) or str(uuid4())
        slug_candidate = base_slug
        index = 1
        while (
            ChallengeCategory.objects.filter(slug=slug_candidate)
            .exclude(pk=self.pk)
            .exists()
        ):
            index += 1
            slug_candidate = f"{base_slug}-{index}"
        return slug_candidate

    def save(self, *args, **kwargs):  # pragma: no cover - simple slug helper
        if not self.slug:
            self.slug = self._generate_unique_slug()
        super().save(*args, **kwargs)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.name


class Challenge(models.Model):
    """Represents a scavenger hunt challenge with configurable mechanics."""

    class ChallengeType(models.TextChoices):
        REGULAR = "regular", "Regular"
        EXCLUSIVE = "exclusive", "Exclusive"
        DECREASING = "decreasing", "Decreasing"
        DEPENDENT = "dependent", "Dependent"

    category = models.ForeignKey(
        ChallengeCategory,
        related_name="challenges",
        on_delete=models.CASCADE,
    )
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, blank=True)
    description = models.TextField()
    sort_order = models.PositiveIntegerField(
        default=0,
        help_text="Controls display order within the category (lower numbers appear first).",
    )
    challenge_type = models.CharField(
        max_length=20,
        choices=ChallengeType.choices,
        default=ChallengeType.REGULAR,
    )
    prerequisites = models.ManyToManyField(
        "self",
        symmetrical=False,
        through="ChallengeDependency",
        through_fields=("challenge", "prerequisite"),
        related_name="unlocking_challenges",
        blank=True,
    )
    base_points = models.PositiveIntegerField(default=100)
    decay_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
        help_text="Percentage (0-100) deducted for remaining classes each time this challenge is solved.",
    )
    minimum_points = models.PositiveIntegerField(
        default=0,
        help_text="Floor value for decreasing challenges when decay is applied.",
    )
    answer = models.TextField(
        help_text="Exact answer string that must be submitted to award credit."
    )
    answer_case_sensitive = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    allow_multiple_solves = models.BooleanField(
        default=False,
        help_text="Reserved for future modes; remains False so only one solve per class.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["category", "sort_order", "title"]

    def _generate_unique_slug(self) -> str:
        base_slug = slugify(self.title) or str(uuid4())
        slug_candidate = base_slug
        index = 1
        while (
            Challenge.objects.filter(slug=slug_candidate).exclude(pk=self.pk).exists()
        ):
            index += 1
            slug_candidate = f"{base_slug}-{index}"
        return slug_candidate

    def save(self, *args, **kwargs):  # pragma: no cover - simple slug helper
        if not self.slug:
            self.slug = self._generate_unique_slug()
        super().save(*args, **kwargs)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.title

    def requires_dependencies(self) -> bool:
        return self.challenge_type == self.ChallengeType.DEPENDENT

    def is_exclusive(self) -> bool:
        return self.challenge_type == self.ChallengeType.EXCLUSIVE

    def is_decreasing(self) -> bool:
        return self.challenge_type == self.ChallengeType.DECREASING

    def clean(self):
        if self.challenge_type != self.ChallengeType.DECREASING and self.decay_percent:
            if self.decay_percent > 0:
                raise ValidationError(
                    "Decay percentage applies only to decreasing challenges."
                )

        if (
            self.challenge_type == self.ChallengeType.DECREASING
            and self.decay_percent <= 0
        ):
            raise ValidationError(
                "Decreasing challenges must specify a positive decay percentage."
            )

        if self.minimum_points > self.base_points:
            raise ValidationError("Minimum points cannot exceed base points.")

    def points_for_next_solve(self, solves_count: int) -> int:
        if self.is_decreasing():
            multiplier = (Decimal("100") - self.decay_percent) / Decimal("100")
            value = Decimal(self.base_points) * (multiplier**solves_count)
            quantized = value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            return max(int(quantized), int(self.minimum_points))
        return int(self.base_points)


class ChallengeDependency(models.Model):
    """Represents a prerequisite relationship between challenges."""

    challenge = models.ForeignKey(
        Challenge,
        related_name="dependencies",
        on_delete=models.CASCADE,
    )
    prerequisite = models.ForeignKey(
        Challenge,
        related_name="unlocking",
        on_delete=models.CASCADE,
    )

    class Meta:
        unique_together = ("challenge", "prerequisite")

    def clean(self):
        challenge = self.challenge
        prerequisite = self.prerequisite

        if not challenge or not prerequisite:
            return

        if challenge.pk and prerequisite.pk and challenge.pk == prerequisite.pk:
            raise ValidationError("A challenge cannot depend on itself.")

        challenge_category_id = getattr(challenge, "category_id", None)
        prerequisite_category_id = getattr(prerequisite, "category_id", None)
        if (
            challenge_category_id is not None
            and prerequisite_category_id is not None
            and challenge_category_id != prerequisite_category_id
        ):
            raise ValidationError("Dependencies must be within the same category.")

        if challenge.pk and prerequisite.pk:
            pending_ids = [prerequisite.pk]
            visited_ids: set[int] = set()
            while pending_ids:
                current_ids = pending_ids
                pending_ids = []
                for dependency in ChallengeDependency.objects.filter(
                    challenge_id__in=current_ids
                ).only("prerequisite_id"):
                    if dependency.prerequisite_id == challenge.pk:
                        raise ValidationError(
                            "Circular dependency detected between challenges."
                        )
                    if dependency.prerequisite_id not in visited_ids:
                        visited_ids.add(dependency.prerequisite_id)
                        pending_ids.append(dependency.prerequisite_id)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.challenge} depends on {self.prerequisite}"


class ChallengeSolve(models.Model):
    """Tracks a successful solve for a given class year, awarding points."""

    challenge = models.ForeignKey(
        Challenge,
        related_name="solves",
        on_delete=models.CASCADE,
    )
    participant = models.ForeignKey(
        Participant,
        related_name="challenge_solves",
        on_delete=models.CASCADE,
    )
    team_year = models.PositiveIntegerField(
        help_text="Graduation year representing the solving class."
    )
    awarded_points = models.IntegerField()
    submitted_answer = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("challenge", "team_year")
        ordering = ["-created_at"]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.challenge} solved by {self.team_year} for {self.awarded_points}"


class ChallengeSubmission(models.Model):
    """Tracks every graded answer attempt for a challenge, correct or not."""

    challenge = models.ForeignKey(
        Challenge,
        related_name="submissions",
        on_delete=models.CASCADE,
    )
    participant = models.ForeignKey(
        Participant,
        related_name="challenge_submissions",
        on_delete=models.CASCADE,
    )
    team_year = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Graduation year representing the submitting class, if assigned.",
    )
    submitted_answer = models.TextField()
    is_correct = models.BooleanField(default=False)
    awarded_points = models.IntegerField(
        null=True,
        blank=True,
        help_text="Points awarded, if this submission was correct.",
    )
    solve = models.OneToOneField(
        ChallengeSolve,
        related_name="submission",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:  # pragma: no cover - trivial
        result = "correct" if self.is_correct else "incorrect"
        return f"{self.participant} -> {self.challenge} ({result})"


class DiscordSettings(models.Model):
    """Singleton model for Discord webhook settings."""

    webhook_url = models.URLField(
        max_length=500,
        blank=True,
        help_text="Discord webhook URL for first blood notifications",
    )
    notifications_enabled = models.BooleanField(
        default=False,
        help_text="Enable/disable Discord first blood notifications",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Discord Settings"
        verbose_name_plural = "Discord Settings"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass

    @classmethod
    def load(cls):
        obj, created = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self) -> str:
        return "Discord Settings"


class ShortLink(models.Model):
    """A short redirect alias, used in place of third-party URL shorteners."""

    alias = models.SlugField(
        max_length=64,
        unique=True,
        help_text="The path segment used after /s/, e.g. 'clue1' for /s/clue1/.",
    )
    target_url = models.URLField(
        max_length=2000, help_text="The destination the short link redirects to."
    )
    created_by = models.ForeignKey(
        Participant,
        related_name="short_links",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    click_count = models.PositiveIntegerField(default=0)
    last_accessed_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"/s/{self.alias}/ -> {self.target_url}"
