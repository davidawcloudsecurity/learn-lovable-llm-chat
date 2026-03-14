# Haze Color Palette Theme

## Overview
BringBackTheAssistant now uses the "Haze" color palette - a sophisticated gradient from deep ocean blue to soft lavender, set against a dark navy background.

## Color Palette

### Primary Colors
- **Deep Ocean Blue** `#0F6BAE` - HSL: `207 75% 37%`
  - Primary action buttons
  - Active navigation items
  - Important links
  - Selected states

- **Bright Sky Blue** `#248BD6` - HSL: `207 87% 51%`
  - Hover states for buttons
  - Secondary buttons
  - Interactive elements
  - Progress indicators
  - Primary brand color

- **Light Periwinkle** `#83B8FF` - HSL: `220 100% 76%`
  - Icons and icon backgrounds
  - Badges and tags
  - Subtle highlights
  - Input field borders on focus
  - Accent color

- **Soft Lavender** `#C6CDFF` - HSL: `230 100% 89%`
  - Text on dark backgrounds
  - Disabled button text
  - Placeholder text
  - Subtle dividers

### Background
- **Dark Navy** `#2C3E50` - HSL: `210 25% 18%`
  - Main background color (dark mode)
  - Creates professional, calming atmosphere

## Usage in Code

### CSS Variables
```css
--haze-deep-blue: 207 75% 37%;
--haze-sky-blue: 207 87% 51%;
--haze-periwinkle: 220 100% 76%;
--haze-lavender: 230 100% 89%;
--haze-navy-bg: 210 25% 25%;
```

### Tailwind Classes
Use standard Tailwind classes with the theme:
- `bg-primary` - Bright Sky Blue
- `bg-accent` - Light Periwinkle
- `text-foreground` - Soft Lavender (dark mode)
- `border-border` - Subtle navy borders

### Custom Utilities
- `.ds-wordmark` - Logo styling with gradient background
- `.ds-card-hover` - Card hover effects with blue glow
- `.ds-wave-overlay` - Subtle blue gradient overlays
- `.story-link` - Animated underline links in sky blue

## Dark Mode
Dark mode is enabled by default to showcase the navy background. The theme automatically applies the Haze palette with optimal contrast ratios.

## Design Principles
1. **Calming & Professional** - Navy background reduces eye strain
2. **Clear Hierarchy** - Blue gradient guides user attention
3. **High Contrast** - Lavender text on navy ensures readability
4. **Interactive Feedback** - Sky blue hover states provide clear affordance
5. **Cohesive Branding** - Consistent blue palette throughout

## Examples

### Button Styles
- Primary: Sky Blue background → Deep Blue on hover
- Secondary: Periwinkle border → Sky Blue fill on hover
- Ghost: Transparent → Periwinkle background on hover

### Card Styles
- Background: Slightly lighter navy
- Border: Periwinkle with low opacity
- Hover: Sky blue glow shadow

### Text Hierarchy
- Headings: Lavender (high contrast)
- Body: Slightly muted lavender
- Secondary: Periwinkle
- Links: Sky Blue → Periwinkle on hover

## Accessibility
- All color combinations meet WCAG AA standards for contrast
- Interactive elements have clear visual feedback
- Focus states use the bright sky blue ring
