# option-tools
Calculates option statistics using Yahoo finance, Barcharts and Market Cameleon sources.


```python
import strategy_helper as s
from IPython.display import display
import marketchameleon as mc

display(s.assemble_stock_list(s.add_volume_data(mc.get_option_list()),"allstocks.pk1"))
```
![Alt text](/table.png?raw=true "Selected options")

```python
import yahoo_tools as y
equities=y.load_history_data("allstocks.pk1")
s.draw_histogram("INTC",5,equities)
```

![Alt text](/histogram.png?raw=true "Selected options")
